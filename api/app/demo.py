"""Demo mode: simulated carrier / customers / agents (the "sim" Asterisk, driven over ARI) plus the
aggregated state used by the demo pages.

Real calls do not need any of this: customers and agents can use the browser softphone (SIP.js, WebRTC).
The simulation exists so that every scenario can also run with buttons only (no microphone), for automated
tests, and for load (N concurrent customers).

Only one ARI client may serve the Stasis app "sim" at a time -> tests/e2e.py calls POST /demo/disable first.
"""
import asyncio
import json
import logging
import os
import random
import re
import time
from pathlib import Path

import httpx
import websockets
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config
from .labels import IVR_MENU, QUEUE_TH, SERVICE_DIGIT

log = logging.getLogger("demo")

SIM_HOST = os.getenv("SIM_HOST", "sim")
SIM_PORT = int(os.getenv("SIM_PORT", "8088"))
ARI_USER, ARI_PASS = "poc", "poc-ari-secret"
AGENTS = ["1101", "1102", "1103"]                      # simulated agents (answered through ARI)
IVR_DIGIT = SERVICE_DIGIT                              # accident=1 ... club=6
CUSTOMER_POOL = int(os.getenv("CUSTOMER_POOL", "20"))
POOL = [f"0800000{n:03d}" for n in range(1, CUSTOMER_POOL + 1)]   # numbers a customer can use (web phone or simulated)
POOL_STALE = 20                                        # seconds without heartbeat before a claimed number is free again
# magic numbers for regression tests: the carrier chooses the outcome from the last digit (not shown in the UI)
OUTBOUND_NUMBER = {
    "connected": "0900000001", "remote_hangup": "0900000006", "busy": "0900000002",
    "no_answer": "0900000003", "cancel": "0900000003", "congestion": "0900000004", "invalid": "0900000005",
}
ANSWER_DELAY = float(os.getenv("DEMO_ANSWER_DELAY", "3"))     # seconds a sim agent "takes" to pick up
AGENT_CHAN = re.compile(r"^PJSIP/(\d{4})-")


class Sim:
    def __init__(self, ami) -> None:
        self.ami = ami
        self.http = httpx.AsyncClient(base_url=f"http://{SIM_HOST}:{SIM_PORT}/ari", auth=(ARI_USER, ARI_PASS), timeout=10)
        self.task: asyncio.Task | None = None
        self.connected = False
        self.alive: set[str] = set()
        self.agent_ch: dict[str, str] = {}      # ext -> sim channel id of the call currently on that agent
        self.answered: set[str] = set()
        self.auto = False                       # False = manual: the presenter presses keys / answers calls
        self.quick: set[str] = set()            # agents that answer a click-to-call leg immediately (also in manual mode)
        self.customers: dict[str, str] = {}     # caller number -> sim channel of its (simulated) inbound call
        self.pressed: set[str] = set()          # callers that already chose an IVR service
        self.talking: set[str] = set()          # customer channels already playing speech
        self.menu_ready: set[str] = set()       # callers whose IVR menu is listening (from IvrPrompt events)
        self.pool: dict[str, dict] = {}         # claimed customer numbers: {"mode", "last"}
        self.incoming: dict[str, dict] = {}     # simulated customer number -> ringing outbound call {"ch","from","ts"}
        self.cust_out: dict[str, str] = {}      # simulated customer number -> answered outbound call channel
        self._tasks: set[asyncio.Task] = set()
        self._seq = 0
        ami.listeners.append(self._on_ami)

    @property
    def enabled(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self) -> None:
        if not self.enabled:
            self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass
        self.task, self.connected = None, False
        for s in (self.alive, self.answered, self.quick, self.pressed, self.talking, self.menu_ready):
            s.clear()
        for d in (self.agent_ch, self.customers, self.incoming, self.cust_out):
            d.clear()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    # ---------- AMI events: which callers are listening to the IVR menu ----------
    def _on_ami(self, e: dict) -> None:
        n, who = e["event"], e.get("CallerIDNum")
        if n == "IvrPrompt":
            if e.get("Prompt", "").strip() == "th-menu":
                self.menu_ready.add(who)
            else:
                self.menu_ready.discard(who)
        elif n in ("QueueCallerJoin", "Hangup"):
            self.menu_ready.discard(who)
        elif n == "DTMFEnd" and e.get("Direction") == "Received":
            self.menu_ready.discard(who)

    # ---------- ARI plumbing ----------
    async def _run(self) -> None:
        url = f"ws://{SIM_HOST}:{SIM_PORT}/ari/events?app=sim&api_key={ARI_USER}:{ARI_PASS}"
        while True:
            try:
                async with websockets.connect(url) as ws:
                    self.connected = True
                    async for raw in ws:
                        self._dispatch(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("sim ARI connection: %s", exc)
            self.connected = False
            await asyncio.sleep(3)

    def _dispatch(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "StasisStart":
            args, ch = ev.get("args") or [], ev["channel"]["id"]
            if args[:1] == ["agent"]:
                self._spawn(self._agent_flow(ch, args[1]))
            elif args[:1] == ["customer"]:
                self._spawn(self._customer_flow(ch, args[1], ev["channel"]["caller"]["number"]))
            elif args[:1] == ["customer_out"]:
                self._spawn(self._customer_out_flow(ch, args[1], ev["channel"]["caller"]["number"]))
        elif t in ("ChannelDestroyed", "StasisEnd"):
            ch = ev["channel"]["id"]
            self.alive.discard(ch)
            for ext, c in list(self.agent_ch.items()):
                if c == ch:
                    del self.agent_ch[ext]
                    self.answered.discard(ext)
            self.talking.discard(ch)
            for num, c in list(self.customers.items()):
                if c == ch:
                    del self.customers[num]
                    self.pressed.discard(num)
            for num, c in list(self.cust_out.items()):
                if c == ch:
                    del self.cust_out[num]
            for num, inc in list(self.incoming.items()):
                if inc["ch"] == ch:
                    del self.incoming[num]

    async def ari(self, method: str, path: str, **params):
        r = await self.http.request(method, path, params=params)
        r.raise_for_status()
        return r.json() if r.content else None

    async def _talk(self, ch: str) -> None:
        """Speech on the channel so the recording contains audio from both parties."""
        try:
            await self.ari("POST", f"/channels/{ch}/play", media=["sound:hello-world"] * 30)
        except Exception:
            pass

    # ---------- simulated agents ----------
    async def _agent_flow(self, ch: str, ext: str) -> None:
        self.alive.add(ch)
        self.agent_ch[ext] = ch
        self.answered.discard(ext)
        quick = ext in self.quick
        self.quick.discard(ext)
        if not self.auto and not quick:
            return                              # manual: wait for POST /demo/agent/{ext}/answer
        await asyncio.sleep(1.0 if quick else ANSWER_DELAY)
        if ch not in self.alive or ext in self.answered:
            return
        try:
            await self.answer(ext)
        except Exception:
            return

    async def answer(self, ext: str) -> None:
        ch = self.agent_ch.get(ext)
        if not ch or ch not in self.alive or ext in self.answered:
            raise HTTPException(409, f"agent {ext} has no ringing call")
        self.answered.add(ext)
        await self.ari("POST", f"/channels/{ch}/answer")
        await self._talk(ch)

    async def reject(self, ext: str) -> None:
        ch = self.agent_ch.get(ext)
        if not ch or ch not in self.alive or ext in self.answered:
            raise HTTPException(409, f"agent {ext} has no ringing call")
        await self.hangup_channel(ch, reason="busy")

    def ringing(self, ext: str) -> bool:
        ch = self.agent_ch.get(ext)
        return bool(ch) and ch in self.alive and ext not in self.answered

    # ---------- simulated customers: inbound (customer dials 1484) ----------
    async def press(self, caller: str, digit: str) -> None:
        ch = self.customers.get(caller)
        if not ch or ch not in self.alive:
            raise HTTPException(409, f"no active customer call {caller}")
        if caller not in self.menu_ready:
            raise HTTPException(409, "IVR menu is not listening yet")
        await asyncio.sleep(0.3)                  # Background() starts right after the prompt event
        await self.ari("POST", f"/channels/{ch}/dtmf", dtmf=digit)
        if digit in IVR_MENU:
            self.pressed.add(caller)
        if ch not in self.talking:
            self.talking.add(ch)
            await self._talk(ch)

    async def _customer_flow(self, ch: str, digit: str, caller: str) -> None:
        self.alive.add(ch)
        if digit == "-":                          # manual: the presenter presses the key
            return
        for _ in range(100):                      # wait (<= 30 s) until the IVR menu is listening
            if ch not in self.alive:
                return
            if caller in self.menu_ready:
                break
            await asyncio.sleep(0.3)
        else:
            return
        await asyncio.sleep(1.0)                  # let the audience see "IVR" before the key press
        try:
            await self.press(caller, digit)
        except Exception:
            return

    async def inbound(self, service: str | None, caller: str | None) -> str:
        caller = caller or "08" + "".join(random.choices("0123456789", k=8))
        if caller in self.customers:
            raise HTTPException(409, f"{caller} is already in a call")
        self._seq += 1
        ch = f"demo-cust-{caller}-{self._seq}"
        self.customers[caller] = ch
        try:
            await self.ari("POST", "/channels", endpoint="PJSIP/1484@pbx", app="sim",
                           appArgs=f"customer,{IVR_DIGIT[service] if service else '-'}",
                           callerId=caller, channelId=ch, timeout=30)
        except Exception as exc:
            self.customers.pop(caller, None)
            raise HTTPException(502, f"sim ARI error: {exc}")
        return caller

    # ---------- simulated customers: outbound (the call center dials the customer) ----------
    def sim_online(self, number: str) -> bool:
        p = self.pool.get(number)
        return bool(p and p["mode"] == "sim" and time.time() - p["last"] < POOL_STALE)

    async def _customer_out_flow(self, ch: str, number: str, caller: str) -> None:
        """The carrier handed an outbound call for a customer number that has no web phone registered."""
        self.alive.add(ch)
        if not self.sim_online(number) or number in self.incoming:
            try:                                  # nobody there -> back to the dialplan: Hangup(1) = unallocated
                await self.ari("POST", f"/channels/{ch}/continue")
            except Exception:
                pass
            return
        self.incoming[number] = {"ch": ch, "from": caller, "ts": time.time()}
        try:
            await self.ari("POST", f"/channels/{ch}/ring")
        except Exception:
            pass

    async def customer_answer(self, number: str) -> None:
        inc = self.incoming.pop(number, None)
        if not inc or inc["ch"] not in self.alive:
            raise HTTPException(409, "no incoming call")
        self.cust_out[number] = inc["ch"]
        await self.ari("POST", f"/channels/{inc['ch']}/answer")
        await self._talk(inc["ch"])

    async def customer_reject(self, number: str) -> None:
        inc = self.incoming.pop(number, None)
        if not inc or inc["ch"] not in self.alive:
            raise HTTPException(409, "no incoming call")
        await self.hangup_channel(inc["ch"], reason="busy")

    # ---------- common ----------
    async def hangup_channel(self, ch: str | None, **params) -> None:
        if ch and ch in self.alive:
            try:
                await self.ari("DELETE", f"/channels/{ch}", **params)
            except httpx.HTTPStatusError:
                pass

    async def hold(self, ext: str, on: bool) -> None:
        ch = self.agent_ch.get(ext)
        if not ch or ch not in self.alive or ext not in self.answered:
            raise HTTPException(409, f"agent {ext} has no active call")
        await self.ari("POST" if on else "DELETE", f"/channels/{ch}/hold")

    async def cancel_when_ringing(self, ext: str, after: float = 5.0) -> None:
        """Agent gives up while the far end is still ringing (-> result CANCELLED)."""
        for _ in range(60):
            if ext in self.answered:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(after)
        await self.hangup_channel(self.agent_ch.get(ext))

    # ---------- customer number pool ----------
    def claim(self, mode: str) -> str:
        now = time.time()
        for n in POOL:
            p = self.pool.get(n)
            if not p or now - p["last"] > POOL_STALE:
                self.pool[n] = {"mode": mode, "last": now}
                return n
        raise HTTPException(409, "all customer numbers are in use")

    def heartbeat(self, number: str, mode: str | None = None) -> None:
        if number not in POOL:
            raise HTTPException(404, "unknown customer number")
        p = self.pool.setdefault(number, {"mode": mode or "sim", "last": 0})
        if mode:
            p["mode"] = mode
        p["last"] = time.time()

    def release(self, number: str) -> None:
        self.pool.pop(number, None)
        self.incoming.pop(number, None)


sim: Sim | None = None
ami = None


def init(ami_service) -> None:
    global sim, ami
    ami, sim = ami_service, Sim(ami_service)


async def shutdown() -> None:
    if sim:
        await sim.stop()
        await sim.http.aclose()


def require_key(x_api_key: str | None = Header(default=None), api_key: str | None = Query(default=None)):
    if (x_api_key or api_key) != config.API_KEY:
        raise HTTPException(401, "invalid api key")


router = APIRouter(prefix="/demo", tags=["demo"])
auth = [Depends(require_key)]
STATIC = Path(__file__).parent / "static"


# ============================================================ pages
def _page(name: str):
    async def handler():
        return FileResponse(STATIC / f"{name}.html", media_type="text/html")
    return handler


for _route, _file in (("", "index"), ("/customer", "customer"), ("/agent", "agent"), ("/dashboard", "dashboard"),
                      ("/transactions", "transactions"), ("/flow", "flow")):
    router.add_api_route(_route, _page(_file), methods=["GET"], include_in_schema=False)


@router.get("/config")
async def demo_config():
    """Front-end settings. The browser derives the WebSocket URLs from the host it was loaded from."""
    return {"api_key": config.API_KEY, "pbx_ws_port": 8088, "sim_ws_port": 8089, "domain_hint": "localhost",
            "number": "1484", "ivr_menu": IVR_MENU, "queue_th": QUEUE_TH, "pool": POOL,
            "sim_agents": AGENTS, "outbound_prefix": "9"}


def _need_demo() -> Sim:
    if not sim or not sim.enabled:
        raise HTTPException(409, "Demo mode is off")
    return sim


@router.post("/enable", dependencies=auth)
async def enable():
    from . import main as m
    sim.start()
    for ext in AGENTS:                              # agents log in to all queues and are available
        await m.agent_login(ext)
        try:
            await m.agent_pause(ext, False)
        except HTTPException:
            pass
    return {"enabled": True}


@router.post("/disable", dependencies=auth)
async def disable():
    await sim.stop()
    return {"enabled": False}


class InboundBody(BaseModel):
    service: str | None = None      # None = manual (the user presses the IVR key); else accident|incident|motor|insurance|sales|club
    caller: str | None = None


@router.post("/inbound", dependencies=auth)
async def demo_inbound(body: InboundBody):
    if body.service is not None and body.service not in IVR_DIGIT:
        raise HTTPException(422, f"service must be one of {', '.join(IVR_DIGIT)}")
    s = _need_demo()
    return {"caller": await s.inbound(body.service, body.caller)}


class BatchBody(BaseModel):
    count: int = 5
    service: str | None = None      # None / "random" = every caller picks a random service
    interval: float = 0.4           # seconds between two callers


@router.post("/inbound/batch", dependencies=auth)
async def demo_inbound_batch(body: BatchBody):
    """N customers call 1484 at (almost) the same time: IVR -> queue -> agents, all in parallel."""
    s = _need_demo()
    if not 1 <= body.count <= 30:
        raise HTTPException(422, "count must be 1-30")
    if body.service not in (None, "random") and body.service not in IVR_DIGIT:
        raise HTTPException(422, f"service must be random or one of {', '.join(IVR_DIGIT)}")

    async def run():
        for _ in range(body.count):
            svc = random.choice(list(IVR_DIGIT)) if body.service in (None, "random") else body.service
            try:
                await s.inbound(svc, None)
            except HTTPException as exc:
                log.warning("batch caller failed: %s", exc.detail)
            await asyncio.sleep(body.interval)

    s._spawn(run())
    return {"started": body.count}


class PressBody(BaseModel):
    digit: str


@router.post("/customer/{caller}/press", dependencies=auth)
async def demo_press(caller: str, body: PressBody):
    if body.digit not in list("0123456789"):
        raise HTTPException(422, "digit must be 0-9")
    await _need_demo().press(caller, body.digit)
    return {"caller": caller, "digit": body.digit}


@router.post("/customer/{caller}/hangup", dependencies=auth)
async def demo_customer_hangup(caller: str):
    s = _need_demo()
    await s.hangup_channel(s.customers.get(caller) or s.cust_out.get(caller))
    return {"caller": caller}


@router.post("/customer/{number}/answer", dependencies=auth)
async def demo_customer_answer(number: str):
    await _need_demo().customer_answer(number)
    return {"number": number}


@router.post("/customer/{number}/reject", dependencies=auth)
async def demo_customer_reject(number: str):
    await _need_demo().customer_reject(number)
    return {"number": number}


class ClaimBody(BaseModel):
    mode: str = "sim"               # sim | web


@router.post("/customers/claim", dependencies=auth)
async def customers_claim(body: ClaimBody):
    if body.mode not in ("sim", "web"):
        raise HTTPException(422, "mode must be sim or web")
    n = sim.claim(body.mode)
    return {"number": n, "password": f"poc-{n}", "mode": body.mode}


@router.post("/customers/{number}/heartbeat", dependencies=auth)
async def customers_heartbeat(number: str, mode: str | None = None):
    sim.heartbeat(number, mode)
    return {"number": number}


@router.post("/customers/{number}/release", dependencies=auth)
async def customers_release(number: str):
    sim.release(number)
    return {"number": number}


@router.get("/customers", dependencies=auth)
async def customers_list():
    """Customer numbers that are currently online (a browser has claimed them), usable as outbound targets."""
    now = time.time()
    return [{"number": n, "mode": p["mode"]} for n, p in sorted(sim.pool.items()) if now - p["last"] < POOL_STALE]


@router.get("/customer/{number}", dependencies=auth)
async def customer_state(number: str):
    """Poll target of a simulated customer page: ringing outbound call + progress of its own call."""
    snap = await _snapshot()
    call = next((c for c in snap["calls"] if c["number"] == number), None)
    inc = sim.incoming.get(number)
    return {"number": number, "call": call, "menu_ready": number in sim.menu_ready,
            "incoming": {"from": inc["from"]} if inc and inc["ch"] in sim.alive else None}


@router.post("/agent/{ext}/answer", dependencies=auth)
async def demo_answer(ext: str):
    await _need_demo().answer(ext)
    return {"agent": ext}


@router.post("/agent/{ext}/reject", dependencies=auth)
async def demo_reject(ext: str):
    await _need_demo().reject(ext)
    return {"agent": ext}


@router.post("/agent/{ext}/hold", dependencies=auth)
async def demo_hold(ext: str, on: bool = True):
    await _need_demo().hold(ext, on)
    return {"agent": ext, "hold": on}


class AutoBody(BaseModel):
    auto: bool


@router.post("/auto", dependencies=auth)
async def demo_auto(body: AutoBody):
    _need_demo().auto = body.auto
    return {"auto": body.auto}


class OutboundBody(BaseModel):
    agent: str
    number: str | None = None       # any number; 08000000NN = customer pool (real web phone or simulated customer)
    outcome: str | None = None      # regression tests only: magic number 09... (connected | busy | no_answer | ...)


@router.post("/outbound", dependencies=auth)
async def demo_outbound(body: OutboundBody):
    """Click-to-call: the agent's phone rings first, then the number is dialed through the carrier."""
    from . import main as m
    if body.outcome:
        if body.outcome not in OUTBOUND_NUMBER:
            raise HTTPException(422, "unknown outcome")
        number = OUTBOUND_NUMBER[body.outcome]
    elif body.number:
        number = body.number
    else:
        raise HTTPException(422, "number is required")
    s = sim
    if body.agent in AGENTS:
        _need_demo().quick.add(body.agent)
    await m.click_to_call(m.OriginateBody(agent=body.agent, destination=number))
    if body.outcome == "cancel" and s:
        s._spawn(s.cancel_when_ringing(body.agent))
    return {"dialed": number}


# ============================================================ aggregated state for the demo pages
def _dialed_number(call_id: str) -> str:
    for e in reversed(ami.recent):
        if e["event"] == "DialBegin" and e.get("Linkedid") == call_id and e.get("DestChannel", "").startswith("PJSIP/trunk-"):
            return e.get("DialString", "").split("@")[0]
    return ""


def _queue_of(call_id: str) -> str | None:
    for e in reversed(ami.recent):
        if e["event"] == "QueueCallerJoin" and e.get("Linkedid") == call_id:
            return e.get("Queue")
    return None


def _seconds(hms: str | None) -> int:
    try:
        h, m, s = (int(x) for x in (hms or "").split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return 0


def _group_calls(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["call_id"], []).append(r)
    calls = []
    for cid, chs in groups.items():
        trunk = [c for c in chs if (c["channel"] or "").startswith("PJSIP/trunk-sim-")]
        agents = [c for c in chs if AGENT_CHAN.match(c["channel"] or "")]
        outbound = any(c["context"] == "sub-outbound" for c in chs)
        bridged_agent = next((a for a in agents if a["bridge_id"]), None)
        ext = AGENT_CHAN.match((bridged_agent or (agents[0] if agents else {"channel": ""}))["channel"])
        agent = ext.group(1) if ext else None
        if outbound or (agents and not trunk):
            if not agents:
                continue
            kind = "outbound"
            stage = "talking" if bridged_agent else ("dialing" if any(a["application"] == "Dial" for a in agents) else "connecting_agent")
            who = _dialed_number(cid)
        elif trunk:
            kind = "inbound"
            if bridged_agent:
                stage = "talking"
            elif any((c["application"] or "").lower() == "queue" for c in trunk):
                stage = "queue"
            else:
                stage = "ivr"
            who = trunk[0]["caller"]
        else:
            continue
        held = any(c["channel"] in ami.held for c in chs)
        if held and stage == "talking":
            stage = "hold"
        queue = _queue_of(cid) if kind == "inbound" else None
        calls.append({"call_id": cid, "direction": kind, "stage": stage, "number": who, "agent": agent,
                      "menu_ready": who in sim.menu_ready,
                      "can_press": stage == "ivr" and who in sim.menu_ready and who not in sim.pressed and who in sim.customers,
                      "queue": queue, "queue_th": QUEUE_TH.get(queue, queue),
                      "duration": max(_seconds(c["duration"]) for c in chs)})
    return sorted(calls, key=lambda c: c["call_id"])


def _agent_state(a: dict) -> str:
    if not a["registered"]:
        return "offline"
    if not a["queues"]:
        return "logged_out"
    if any(q["paused"] for q in a["queues"].values()):
        return "paused"
    ds = (a["device_state"] or "").lower()
    if ds in ("in use", "busy"):
        return "busy"
    if ds == "ringing":
        return "ringing"
    return "idle"


def _event_text(e: dict) -> tuple[str, str] | None:
    n, ch = e["event"], e.get("Channel", "")
    e = {**e, "Queue": QUEUE_TH.get(e.get("Queue"), e.get("Queue"))}
    m = AGENT_CHAN.match(ch)
    ext = m.group(1) if m else (e.get("Interface", "") or "").split("/")[-1]
    if n == "IvrPrompt":
        from .labels import PROMPT_TH
        p = e.get("Prompt", "").strip()
        return "ivr", f"IVR เล่นเสียง «{PROMPT_TH.get(p, p)}» ให้ {e.get('CallerIDNum')}"
    if n == "DTMFEnd" and e.get("Direction") == "Received" and ch.startswith("PJSIP/trunk-sim-"):
        label = IVR_MENU.get(e.get("Digit"), {}).get("th")
        return "ivr", f"ลูกค้า {e.get('CallerIDNum')} กด {e.get('Digit')}" + (f" ({label})" if label else "")
    if n == "QueueCallerJoin":
        return "queue", f"ลูกค้า {e.get('CallerIDNum')} เข้าคิว {e.get('Queue')} (ลำดับที่ {e.get('Position')})"
    if n == "AgentCalled":
        return "queue", f"คิว {e.get('Queue')} เรียก agent {ext}"
    if n == "AgentRingNoAnswer":
        return "warn", f"Agent {ext} ไม่รับสาย → ส่งต่อ agent ถัดไป"
    if n == "AgentConnect":
        return "ok", f"Agent {ext} รับสาย {e.get('CallerIDNum')} (รอคิว {e.get('HoldTime')} วินาที)"
    if n == "AgentComplete":
        return "info", f"Agent {ext} จบสาย คุย {e.get('TalkTime')} วินาที"
    if n == "QueueCallerAbandon":
        return "warn", f"ลูกค้า {e.get('CallerIDNum')} วางสายก่อนมี agent รับ (ทิ้งสาย)"
    if n == "Hold" and m:
        return "info", f"Agent {ext} พักสาย (Hold)"
    if n == "Unhold" and m:
        return "info", f"Agent {ext} กลับมาคุยต่อ (Unhold)"
    if n == "BlindTransfer":
        t = e.get("Extension", "")
        return "info", f"โอนสายจาก {e.get('TransfererCallerIDNum')} ไปยัง {QUEUE_TH.get(t, t)}"
    if n == "OutboundDialResult":
        return "ok" if e["result"] == "CONNECTED" else "warn", f"Agent {e['agent']} โทรออก {e['dialed']} → {e['result']}"
    return None


_snap: dict = {"t": 0.0, "v": None}
_snap_lock = asyncio.Lock()


async def _snapshot() -> dict:
    """agents + queues + calls, shared by all pollers (cached ~0.8 s so 20 open pages do not flood AMI)."""
    from . import main as m
    async with _snap_lock:
        if _snap["v"] is None or time.time() - _snap["t"] > 0.8:
            ags, qs, rows = await asyncio.gather(m.agents(), m._queues(), m.active_calls())
            _snap["v"] = {"agents": ags, "queues": qs, "calls": _group_calls(rows)}
            _snap["t"] = time.time()
        return _snap["v"]


@router.get("/state", dependencies=auth)
async def state():
    from . import agents_admin, main as m
    snap = await _snapshot()
    ags, qs, calls = snap["agents"], snap["queues"], snap["calls"]
    web = {a["ext"]: a for a in agents_admin.load()}
    agents = []
    for a in ags:
        ext = a["extension"]
        if not (a["registered"] or ext in AGENTS or ext in web):
            continue
        agents.append({"extension": ext, "state": _agent_state(a), "simulated": ext in AGENTS,
                       "kind": "sim" if ext in AGENTS else ("web" if ext in web else "softphone"),
                       "name": web[ext]["name"] if ext in web else f"Agent {ext}",
                       "registered": a["registered"], "logged_in": bool(a["queues"]),
                       "sim_ringing": sim.ringing(ext),
                       "call": next((c for c in calls if c["agent"] == ext), None),
                       "calls_taken": max((q["calls_taken"] for q in a["queues"].values()), default=0)})
    events = [{"ts": e["ts"], "kind": k, "text": t} for e in list(ami.recent)[-400:] if (r := _event_text(e)) for k, t in [r]]
    last_out = next((e for e in reversed(ami.recent) if e["event"] == "OutboundDialResult"), None)
    return {
        "ami_connected": ami.connected,
        "demo": {"enabled": sim.enabled, "sim_connected": sim.connected, "auto": sim.auto},
        "kpi": {
            "active_calls": len(calls),
            "waiting": sum(q["waiting"] for q in qs),
            "completed": sum(q["completed"] for q in qs),
            "abandoned": sum(q["abandoned"] for q in qs),
            "agents": {s: sum(1 for a in agents if a["state"] == s)
                       for s in ("idle", "busy", "ringing", "paused", "logged_out", "offline")},
        },
        "queues": [{"name": q["name"], "name_th": QUEUE_TH.get(q["name"], q["name"]), "waiting": q["waiting"],
                    "members": len(q["members"]), "callers": q["callers"],
                    "completed": q["completed"], "abandoned": q["abandoned"]} for q in qs],
        "agents": agents,
        "calls": calls,
        "events": events[-40:][::-1],
        "last_outbound": last_out,
        "screenpop": m.crm_inbox[-3:][::-1],
    }
