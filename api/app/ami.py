"""AMI client wrapper: actions (queue/agent/call control), event bus, CRM webhook."""
import asyncio
import logging
from collections import deque
from datetime import datetime, timezone

import httpx
from panoramisk import Manager

from . import config
from .store import _outbound_result
log = logging.getLogger("ami")

# AMI events forwarded to API consumers (WebSocket / /events)
INTERESTING = {
    "Newchannel", "Hangup", "DialBegin", "DialEnd", "BridgeEnter", "BridgeLeave",
    "DTMFEnd", "Hold", "Unhold", "BlindTransfer", "AttendedTransfer",
    "QueueCallerJoin", "QueueCallerLeave", "QueueCallerAbandon",
    "AgentCalled", "AgentConnect", "AgentComplete", "AgentRingNoAnswer", "AgentDump",
    "QueueMemberAdded", "QueueMemberRemoved", "QueueMemberPause", "QueueMemberStatus",
    "ContactStatus", "Cdr",
    "UserEvent",          # dialplan UserEvent(IvrPrompt,Prompt: ...) -> converted to IvrPrompt below
}
NOISE_KEYS = {"Privilege", "ActionID", "SystemName", "Uniqueid2"}


class AmiService:
    def __init__(self) -> None:
        self.manager = Manager(
            host=config.AMI_HOST, port=config.AMI_PORT,
            username=config.AMI_USER, secret=config.AMI_SECRET,
            ping_delay=10, ping_attempts=3,
        )
        self.manager.register_event("*", self._on_event)
        self.recent: deque = deque(maxlen=500)
        self.subscribers: set[asyncio.Queue] = set()
        self._http = httpx.AsyncClient(timeout=5)
        self.held: set[str] = set()              # channels currently on hold (demo page)
        self._trunk_dials: dict[str, str] = {}   # carrier-leg uniqueid -> dialed number
        self.listeners: list = []                # callables(evt) fed with every published event (timeline recorder)

    # ---------- lifecycle ----------
    async def start(self) -> None:
        # panoramisk retries the connection itself (reconnect_timeout) if the PBX is not up yet
        self.manager.connect()

    async def stop(self) -> None:
        self.manager.close()
        await self._http.aclose()

    @property
    def connected(self) -> bool:
        return bool(getattr(self.manager, "_connected", False) and getattr(self.manager, "authenticated", False))

    # ---------- actions ----------
    async def action(self, action: dict, as_list: bool = False):
        # as_list=False: panoramisk's heuristics ("Added ... to queue", "successfully queued") would
        # otherwise wait forever for follow-up messages that Asterisk never sends for plain actions.
        if not self.connected:
            raise ConnectionError("AMI not connected")
        return await self.manager.send_action(action, as_list=as_list)

    async def events_list(self, action: dict) -> list[dict]:
        """Run a list-type action and return the payload events (without Start/Complete markers)."""
        res = await self.action(action, as_list=True)
        if not isinstance(res, list):
            res = [res]
        out = []
        for m in res:
            ev = m.get("Event", "")
            if ev and not ev.endswith("Complete"):
                out.append(dict(m))
        return out

    # ---------- events ----------
    def _on_event(self, manager, message) -> None:
        name = message.get("Event")
        if name not in INTERESTING:
            return
        evt = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": name}
        evt.update({k: v for k, v in dict(message).items() if k not in NOISE_KEYS and k != "Event"})
        if name == "UserEvent":
            # AMI eventfilter hides Newexten, so the dialplan announces each IVR prompt with a UserEvent
            if evt.get("UserEvent") != "IvrPrompt":
                return
            evt["event"] = name = "IvrPrompt"
        self._publish(evt)
        if name == "Hold":
            self.held.add(evt.get("Channel", ""))
        elif name in ("Unhold", "Hangup"):
            self.held.discard(evt.get("Channel", ""))
        if name == "DialBegin" and evt.get("DestChannel", "").startswith("PJSIP/trunk-"):
            self._trunk_dials[evt.get("DestUniqueID", "")] = evt.get("DialString", "").split("@")[0]
        if name == "DialEnd" and evt.get("DestChannel", "").startswith("PJSIP/trunk-"):
            self._publish(self._outbound_dial_result(evt, self._trunk_dials.pop(evt.get("DestUniqueID", ""), "")))
        if name == "AgentConnect":
            asyncio.create_task(self._screen_pop(evt))

    def _publish(self, evt: dict) -> None:
        self.recent.append(evt)
        for fn in self.listeners:
            try:
                fn(evt)
            except Exception:
                log.exception("event listener failed")
        for q in list(self.subscribers):
            if not q.full():
                q.put_nowait(evt)

    @staticmethod
    def _outbound_dial_result(evt: dict, dialed: str) -> dict:
        """Real-time outcome of an outbound dial attempt (for the agent app): connected / busy / no answer ..."""
        status = evt.get("DialStatus")
        agent = evt.get("Channel", "").split("/")[-1].split("-")[0]
        return {
            "ts": evt["ts"], "event": "OutboundDialResult", "call_id": evt.get("Linkedid"),
            "agent": agent, "dialed": dialed,
            "dial_status": status, "result": _outbound_result(status, None),
        }

    async def _screen_pop(self, evt: dict) -> None:
        """Demo of CRM integration: tell the CRM who is calling which agent."""
        if not config.CRM_WEBHOOK_URL:
            return
        payload = {
            "type": "screen_pop",
            "ts": evt["ts"],
            "call_id": evt.get("Linkedid"),
            "caller_number": evt.get("CallerIDNum"),
            "queue": evt.get("Queue"),
            "agent_interface": evt.get("Interface"),
            "agent_name": evt.get("MemberName"),
            "wait_seconds": evt.get("HoldTime"),
        }
        try:
            await self._http.post(config.CRM_WEBHOOK_URL, json=payload)
        except Exception as exc:
            log.warning("CRM webhook failed: %s", exc)
