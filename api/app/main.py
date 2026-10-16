"""POC integration API on top of Asterisk AMI + CDR + recordings.

Intended as the seed of the CRM / Agent-application backend: REST for commands & queries,
WebSocket for real-time call/queue/agent events.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agents_admin, config, demo, store, timeline
from .ami import AmiService
from .labels import QUEUES

logging.basicConfig(level=logging.INFO)
ami = AmiService()
ami.listeners.append(timeline.record)
crm_inbox: list[dict] = []          # mock CRM: received screen-pop payloads


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agents_admin.sync()
    await ami.start()
    yield
    await demo.shutdown()
    await ami.stop()


app = FastAPI(title="Asterisk POC API", version="0.1", lifespan=lifespan)
demo.init(ami)
agents_admin.init(ami)
app.include_router(demo.router)
app.include_router(agents_admin.router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


def require_key(x_api_key: str | None = Header(default=None), api_key: str | None = Query(default=None)):
    if (x_api_key or api_key) != config.API_KEY:
        raise HTTPException(401, "invalid api key")


auth = [Depends(require_key)]


async def ami_call(coro):
    try:
        return await coro
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))


def _ok(resp) -> dict:
    d = dict(resp)
    if d.get("Response") not in ("Success", "Goodbye"):
        raise HTTPException(400, d.get("Message", "AMI error"))
    return {"result": d.get("Message", "ok")}


# ============================================================ health
@app.get("/health")
async def health():
    return {"status": "ok", "ami_connected": ami.connected}


# ============================================================ monitoring
@app.get("/agents", dependencies=auth, tags=["monitor"])
async def agents():
    """Agent extensions: SIP registration/device state + queue membership/pause state."""
    eps = await ami_call(ami.events_list({"Action": "PJSIPShowEndpoints"}))
    result = {}
    for e in eps:
        name = e.get("ObjectName", "")
        if name.isdigit():
            result[name] = {"extension": name, "device_state": e.get("DeviceState"),
                            "registered": bool(e.get("Contacts")), "queues": {}}
    for q in await _queues():
        for m in q["members"]:
            ext = m["interface"].split("/")[-1]
            if ext in result:
                result[ext]["queues"][q["name"]] = {
                    "paused": m["paused"], "calls_taken": m["calls_taken"],
                    "in_call": m["in_call"], "status": m["status"],
                }
    return sorted(result.values(), key=lambda x: x["extension"])


async def _queues() -> list[dict]:
    rows = await ami_call(ami.events_list({"Action": "QueueStatus"}))
    queues: dict[str, dict] = {}
    for r in rows:
        ev, qn = r.get("Event"), r.get("Queue")
        if ev == "QueueParams":
            queues[qn] = {"name": qn, "strategy": r.get("Strategy"), "waiting": int(r.get("Calls", 0)),
                          "completed": int(r.get("Completed", 0)), "abandoned": int(r.get("Abandoned", 0)),
                          "avg_hold_seconds": int(r.get("Holdtime", 0)),
                          "avg_talk_seconds": int(r.get("TalkTime", 0)),
                          "members": [], "callers": []}
        elif ev == "QueueMember" and qn in queues:
            queues[qn]["members"].append({
                "interface": r.get("Location"), "name": r.get("Name"), "status": r.get("StatusText") or r.get("Status"),
                "paused": r.get("Paused") == "1", "in_call": r.get("InCall") == "1",
                "calls_taken": int(r.get("CallsTaken", 0)), "last_call": r.get("LastCall"),
            })
        elif ev == "QueueEntry" and qn in queues:
            queues[qn]["callers"].append({
                "position": int(r.get("Position", 0)), "caller": r.get("CallerIDNum"),
                "wait_seconds": int(r.get("Wait", 0)), "channel": r.get("Channel"),
            })
    return list(queues.values())


@app.get("/queues", dependencies=auth, tags=["monitor"])
async def queues():
    return await _queues()


@app.get("/calls/active", dependencies=auth, tags=["monitor"])
async def active_calls():
    rows = await ami_call(ami.events_list({"Action": "CoreShowChannels"}))
    return [{
        "channel": r.get("Channel"), "state": r.get("ChannelStateDesc"), "caller": r.get("CallerIDNum"),
        "connected": r.get("ConnectedLineNum"), "context": r.get("Context"), "exten": r.get("Exten"),
        "application": r.get("Application"), "duration": r.get("Duration"),
        "bridge_id": r.get("BridgeId"), "call_id": r.get("Linkedid"),
    } for r in rows]


@app.get("/monitor/summary", dependencies=auth, tags=["monitor"])
async def summary():
    ags, qs, calls = await agents(), await _queues(), await active_calls()
    return {
        "active_calls": len({c["call_id"] for c in calls}),
        "active_channels": len(calls),
        "agents": {
            "registered": sum(1 for a in ags if a["registered"]),
            "logged_in": sum(1 for a in ags if a["queues"]),
            "in_call": sum(1 for a in ags if (a["device_state"] or "").lower() in ("in use", "busy")
                            or any(q["in_call"] for q in a["queues"].values())),
            "paused": sum(1 for a in ags if any(q["paused"] for q in a["queues"].values())),
        },
        "queues": [{"name": q["name"], "waiting": q["waiting"], "members": len(q["members"]),
                    "completed": q["completed"], "abandoned": q["abandoned"]} for q in qs],
    }


# ============================================================ agent / call control
class QueueRef(BaseModel):
    queue: str | None = None


@app.post("/agents/{ext}/login", dependencies=auth, tags=["agent"])
async def agent_login(ext: str, body: QueueRef = QueueRef()):
    names = [body.queue] if body.queue else QUEUES
    for q in names:
        r = await ami_call(ami.action({"Action": "QueueAdd", "Queue": q, "Interface": f"PJSIP/{ext}",
                                       "MemberName": f"Agent {ext}"}))
        if dict(r).get("Response") != "Success" and "Already there" not in dict(r).get("Message", ""):
            raise HTTPException(400, dict(r).get("Message"))
    return {"result": f"{ext} logged in", "queues": names}


@app.post("/agents/{ext}/logout", dependencies=auth, tags=["agent"])
async def agent_logout(ext: str, body: QueueRef = QueueRef()):
    names = [body.queue] if body.queue else QUEUES
    for q in names:
        await ami_call(ami.action({"Action": "QueueRemove", "Queue": q, "Interface": f"PJSIP/{ext}"}))
    return {"result": f"{ext} logged out", "queues": names}


@app.post("/agents/{ext}/pause", dependencies=auth, tags=["agent"])
async def agent_pause(ext: str, paused: bool = True, reason: str = "break"):
    return _ok(await ami_call(ami.action({"Action": "QueuePause", "Interface": f"PJSIP/{ext}",
                                          "Paused": "true" if paused else "false", "Reason": reason})))


async def _agent_channel(ext: str) -> str:
    rows = await ami_call(ami.events_list({"Action": "CoreShowChannels"}))
    for r in rows:
        if (r.get("Channel", "").startswith(f"PJSIP/{ext}-") and r.get("ChannelStateDesc") == "Up"
                and r.get("BridgeId")):
            return r["Channel"]
    raise HTTPException(404, f"agent {ext} has no bridged call")


class TransferBody(BaseModel):
    target: str          # extension (e.g. "1002") or queue name (e.g. "q-motor")


@app.post("/agents/{ext}/transfer", dependencies=auth, tags=["agent"])
async def agent_transfer(ext: str, body: TransferBody):
    """Blind-transfer the customer currently talking to agent `ext`."""
    chan = await _agent_channel(ext)
    context = "queue-flow" if body.target.startswith("q-") else "from-internal"
    return _ok(await ami_call(ami.action({"Action": "BlindTransfer", "Channel": chan,
                                          "Context": context, "Exten": body.target})))


@app.post("/agents/{ext}/hangup", dependencies=auth, tags=["agent"])
async def agent_hangup(ext: str):
    chan = await _agent_channel(ext)
    return _ok(await ami_call(ami.action({"Action": "Hangup", "Channel": chan, "Cause": "16"})))


class OriginateBody(BaseModel):
    agent: str
    destination: str     # internal extension (<= 4 digits) or external number (routed via prefix 9)


@app.post("/calls/originate", dependencies=auth, tags=["outbound"])
async def click_to_call(body: OriginateBody):
    """Click-to-call: ring the agent first, then dial the destination (external numbers use the outbound route)."""
    if not body.destination.isdigit():
        raise HTTPException(422, "destination must be digits only")
    external = len(body.destination) > 4
    exten = f"9{body.destination}" if external else body.destination
    return _ok(await ami_call(ami.action({
        "Action": "Originate", "Channel": f"PJSIP/{body.agent}", "Context": "from-internal",
        "Exten": exten, "Priority": "1", "Async": "true", "Timeout": "20000",
        "CallerID": f"Dialing <{body.destination}>",
        "Variable": f"AGENT_EXT={body.agent}",
    })))


# ============================================================ CDR / history / recordings
@app.get("/cdr", dependencies=auth, tags=["history"])
async def cdr(src: str | None = None, dst: str | None = None, linkedid: str | None = None,
              date_from: str | None = None, date_to: str | None = None,
              limit: int = Query(100, le=1000), offset: int = 0):
    """Raw CDR legs (one row per bridge leg)."""
    try:
        return store.query_cdr(src, dst, linkedid, date_from, date_to, limit, offset)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/calls/history", dependencies=auth, tags=["history"])
async def history(caller: str | None = None, date_from: str | None = None, date_to: str | None = None,
                  direction: str | None = Query(None, pattern="^(inbound|outbound|internal)$"),
                  limit: int = Query(50, le=500)):
    """One row per call: IVR choice, queue, agent, wait/talk time, result, recording status."""
    try:
        return store.call_history(limit, caller, date_from, date_to, direction)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/calls/outbound", dependencies=auth, tags=["outbound"])
async def outbound_calls(agent: str | None = None, dialed: str | None = None,
                         result: str | None = Query(None, pattern="^(CONNECTED|BUSY|NO_ANSWER|CANCELLED|FAILED|UNKNOWN)$"),
                         date_from: str | None = None, date_to: str | None = None,
                         limit: int = Query(50, le=500)):
    """Outbound calls with result (connected / not connected + why) and recording status."""
    try:
        return store.call_history(limit, None, date_from, date_to, "outbound", agent, result, dialed)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc))


@app.get("/recordings", dependencies=auth, tags=["history"])
async def recordings(date: str | None = Query(None, description="YYYY/MM/DD"),
                     caller: str | None = None, call_id: str | None = None):
    try:
        return store.list_recordings(date, caller, call_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/recordings/{path:path}", dependencies=auth, tags=["history"])
async def recording_file(path: str):
    try:
        return FileResponse(store.resolve_recording(path), media_type="audio/wav")
    except FileNotFoundError:
        raise HTTPException(404, "recording not found")


@app.get("/queue-log", dependencies=auth, tags=["history"])
async def queue_log(limit: int = Query(200, le=2000), queue: str | None = None, event: str | None = None):
    """Raw Asterisk queue_log (ENTERQUEUE, CONNECT, ABANDON, COMPLETEAGENT, ...)."""
    return store.read_queue_log(limit, queue, event)


# ============================================================ transactions (call timeline)
@app.get("/transactions", dependencies=auth, tags=["history"])
async def transactions(limit: int = Query(50, le=300), direction: str | None = Query(None, pattern="^(inbound|outbound)$"),
                       q: str | None = None):
    """One row per call with its journey: IVR digits, queues, agents, transfers (from -> to), result."""
    return timeline.list_calls(limit, direction, q)


@app.get("/transactions/{call_id}", dependencies=auth, tags=["history"])
async def transaction(call_id: str):
    """All steps of one call, each with the module that talks to the next module (customer -> pjsip -> ivr -> queue -> agent ...)."""
    t = timeline.get_call(call_id)
    if not t:
        raise HTTPException(404, "call not found")
    try:
        row = next((r for r in store.call_history(1000) if r["call_id"] == call_id), None)
    except FileNotFoundError:
        row = None
    t["cdr"] = row
    return t


# ============================================================ real-time events
@app.get("/events", dependencies=auth, tags=["events"])
async def recent_events(limit: int = Query(100, le=500), event: str | None = None):
    items = [e for e in ami.recent if not event or e["event"] == event]
    return items[-limit:]


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket, api_key: str | None = None):
    if api_key != config.API_KEY:
        await ws.close(code=4401)
        return
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)
    ami.subscribers.add(q)
    try:
        while True:
            await ws.send_json(await q.get())
    except WebSocketDisconnect:
        pass
    finally:
        ami.subscribers.discard(q)


# ============================================================ mock CRM (receives screen-pop webhooks)
@app.post("/mock-crm/screenpop", tags=["mock-crm"])
async def mock_crm_receive(payload: dict):
    crm_inbox.append(payload)
    del crm_inbox[:-100]
    return {"received": True}


@app.get("/mock-crm/screenpop", dependencies=auth, tags=["mock-crm"])
async def mock_crm_list():
    return crm_inbox
