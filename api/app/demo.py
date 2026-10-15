"""Demo mode: drives the simulated agents / carrier (the "sim" Asterisk, via ARI) from the API,
so the demo page can run complete call scenarios with buttons only (no real phones needed).

Only one ARI client may serve the Stasis app "sim" at a time -> tests/e2e.py calls POST /demo/disable first.
"""
import asyncio
import json
import logging
import os
import random
import re
from pathlib import Path

import httpx
import websockets
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config

log = logging.getLogger("demo")

SIM_HOST = os.getenv("SIM_HOST", "sim")
SIM_PORT = int(os.getenv("SIM_PORT", "8088"))
ARI_USER, ARI_PASS = "poc", "poc-ari-secret"
AGENTS = ["1101", "1102", "1103"]
IVR_DIGIT = {"claim": "1", "policy": "2", "general": "0"}
# outcome chosen by the simulated carrier from the last digit of the dialed number
OUTBOUND_NUMBER = {
    "connected": "0900000001", "remote_hangup": "0900000006", "busy": "0900000002",
    "no_answer": "0900000003", "cancel": "0900000003", "congestion": "0900000004", "invalid": "0900000005",
}
ANSWER_DELAY = float(os.getenv("DEMO_ANSWER_DELAY", "3"))     # seconds a sim agent "takes" to pick up
AGENT_CHAN = re.compile(r"^PJSIP/(\d{4})-")
QUEUE_TH = {"q-claim": "เคลมประกัน", "q-policy": "กรมธรรม์", "q-general": "ทั่วไป"}


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
        self.quick: set[str] = set()            # (auto mode) agents that answer a click-to-call leg immediately
        self.customers: dict[str, str] = {}     # caller number -> sim channel id
        self.pressed: set[str] = set()          # callers that already chose an IVR service
        self.talking: set[str] = set()          # customer channels already playing speech
        self._tasks: set[asyncio.Task] = set()
        self._seq = 0

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
        for s in (self.alive, self.answered, self.quick, self.pressed, self.talking):
            s.clear()
        self.agent_ch.clear()
        self.customers.clear()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

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

    # ---------- simulated behaviours ----------
    async def _agent_flow(self, ch: str, ext: str) -> None:
        self.alive.add(ch)
        self.agent_ch[ext] = ch
        self.answered.discard(ext)
        quick = ext in self.quick
        self.quick.discard(ext)
        if not self.auto:
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

    async def _ivr_ready(self, caller: str) -> bool:
        rows = await self.ami.events_list({"Action": "CoreShowChannels"})
        return any(r.get("CallerIDNum") == caller and (r.get("Application") or "").lower() in ("background", "waitexten")
                   for r in rows)

    async def press(self, caller: str, digit: str) -> None:
        ch = self.customers.get(caller)
        if not ch or ch not in self.alive:
            raise HTTPException(409, f"no active customer call {caller}")
        if not await self._ivr_ready(caller):
            raise HTTPException(409, "IVR menu is not listening yet")
        await self.ari("POST", f"/channels/{ch}/dtmf", dtmf=digit)
        if digit in IVR_DIGIT.values():
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
            if await self._ivr_ready(caller):
                break
            await asyncio.sleep(0.3)
        else:
            return
        await asyncio.sleep(1.0)                  # let the audience see "IVR" before the key press
        try:
            await self.press(caller, digit)
        except Exception:
            return

    # ---------- commands ----------
    async def inbound(self, service: str | None, caller: str | None) -> str:
        caller = caller or "08" + "".join(random.choices("0123456789", k=8))
        if caller in self.customers:
            raise HTTPException(409, f"{caller} is already in a call")
        self._seq += 1
        ch = f"demo-cust-{caller}-{self._seq}"
        self.customers[caller] = ch
        try:
            await self.ari("POST", "/channels", endpoint="PJSIP/8000@pbx", app="sim", appArgs=f"customer,{IVR_DIGIT[service] if service else '-'}",
                           callerId=caller, channelId=ch, timeout=30)
        except Exception as exc:
            self.customers.pop(caller, None)
            raise HTTPException(502, f"sim ARI error: {exc}")
        return caller

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


@router.get("")
async def page():
    return FileResponse(Path(__file__).parent / "static" / "demo.html", media_type="text/html")


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
    service: str | None = None      # None = manual (presenter presses the IVR key); claim | policy | general = auto-press
    caller: str | None = None


@router.post("/inbound", dependencies=auth)
async def demo_inbound(body: InboundBody):
    if body.service is not None and body.service not in IVR_DIGIT:
        raise HTTPException(422, "service must be claim | policy | general")
    s = _need_demo()
    return {"caller": await s.inbound(body.service, body.caller)}


class PressBody(BaseModel):
    digit: str


@router.post("/customer/{caller}/press", dependencies=auth)
async def demo_press(caller: str, body: PressBody):
    if body.digit not in list("0123456789"):
        raise HTTPException(422, "digit must be 0-9")
    await _need_demo().press(caller, body.digit)
    return {"caller": caller, "digit": body.digit}


@router.post("/agent/{ext}/answer", dependencies=auth)
async def demo_answer(ext: str):
    await _need_demo().answer(ext)
    return {"agent": ext}


@router.post("/agent/{ext}/reject", dependencies=auth)
async def demo_reject(ext: str):
    await _need_demo().reject(ext)
    return {"agent": ext}


class AutoBody(BaseModel):
    auto: bool


@router.post("/auto", dependencies=auth)
async def demo_auto(body: AutoBody):
    _need_demo().auto = body.auto
    return {"auto": body.auto}


class OutboundBody(BaseModel):
    agent: str
    outcome: str                    # connected | remote_hangup | busy | no_answer | cancel | congestion | invalid


@router.post("/outbound", dependencies=auth)
async def demo_outbound(body: OutboundBody):
    from . import main as m
    if body.outcome not in OUTBOUND_NUMBER or body.agent not in AGENTS:
        raise HTTPException(422, "unknown agent or outcome")
    s = _need_demo()
    s.quick.add(body.agent)
    number = OUTBOUND_NUMBER[body.outcome]
    await m.click_to_call(m.OriginateBody(agent=body.agent, destination=number))
    if body.outcome == "cancel":
        s._spawn(s.cancel_when_ringing(body.agent))
    return {"dialed": number}


@router.post("/agent/{ext}/hold", dependencies=auth)
async def demo_hold(ext: str, on: bool = True):
    await _need_demo().hold(ext, on)
    return {"agent": ext, "hold": on}


@router.post("/customer/{caller}/hangup", dependencies=auth)
async def demo_customer_hangup(caller: str):
    s = _need_demo()
    await s.hangup_channel(s.customers.get(caller))
    return {"caller": caller}


# ============================================================ aggregated state for the demo page
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
        menu_ready = any((c["application"] or "").lower() in ("background", "waitexten") for c in trunk)
        if held and stage == "talking":
            stage = "hold"
        duration = max(_seconds(c["duration"]) for c in chs)
        calls.append({"call_id": cid, "direction": kind, "stage": stage, "number": who, "agent": agent,
                      "menu_ready": menu_ready,
                      "can_press": stage == "ivr" and menu_ready and who not in sim.pressed and who in sim.customers,
                      "queue": _queue_of(cid) if kind == "inbound" else None, "duration": duration})
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
    if n == "DTMFEnd" and e.get("Direction") == "Received" and ch.startswith("PJSIP/trunk-sim-"):
        return "ivr", f"ลูกค้า {e.get('CallerIDNum')} กด {e.get('Digit')} ในเมนู IVR"
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
        return "info", f"โอนสายไปยัง {e.get('Extension')}"
    if n == "OutboundDialResult":
        return "ok" if e["result"] == "CONNECTED" else "warn", f"Agent {e['agent']} โทรออก {e['dialed']} → {e['result']}"
    return None


@router.get("/state", dependencies=auth)
async def state():
    from . import main as m
    ags, qs, rows = await asyncio.gather(m.agents(), m._queues(), m.active_calls())
    calls = _group_calls(rows)
    agents = []
    for a in ags:
        if not (a["registered"] or a["extension"] in AGENTS):
            continue
        st = _agent_state(a)
        agents.append({"extension": a["extension"], "state": st, "simulated": a["extension"] in AGENTS,
                       "sim_ringing": sim.ringing(a["extension"]),
                       "call": next((c for c in calls if c["agent"] == a["extension"]), None),
                       "calls_taken": max((q["calls_taken"] for q in a["queues"].values()), default=0)})
    events = [{"ts": e["ts"], "kind": k, "text": t} for e in list(ami.recent)[-300:] if (r := _event_text(e)) for k, t in [r]]
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
        "queues": [{"name": q["name"], "waiting": q["waiting"], "members": len(q["members"]),
                    "callers": q["callers"]} for q in qs],
        "agents": agents,
        "calls": calls,
        "events": events[-25:][::-1],
        "last_outbound": last_out,
        "screenpop": m.crm_inbox[-3:][::-1],
    }
