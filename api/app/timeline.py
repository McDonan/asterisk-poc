"""Call timeline ("transactions"): every AMI event of a call is normalised into a step
(who -> whom, what, which module talks to which module) and stored in SQLite, so a call can be replayed
step by step: IVR prompt -> key pressed -> queue -> agent rings -> answer -> hold -> TRANSFER (from -> to) -> end.
"""
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config
from .labels import IVR_MENU, MODULES, PROMPT_TH, QUEUE_TH

log = logging.getLogger("timeline")
AGENT_CHAN = re.compile(r"^PJSIP/(\d{4})-")
TRUNK_CHAN = re.compile(r"^PJSIP/trunk-sim-")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        try:
            Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
            path = str(Path(config.DATA_DIR) / "timeline.db")
            _conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        except (OSError, sqlite3.Error):
            _conn = sqlite3.connect(":memory:", check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""CREATE TABLE IF NOT EXISTS steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT, call_id TEXT NOT NULL, ts TEXT NOT NULL, kind TEXT NOT NULL,
            frm TEXT, "to" TEXT, text TEXT, detail TEXT)""")
        _conn.execute("CREATE INDEX IF NOT EXISTS steps_call ON steps(call_id, id)")
    return _conn


def _agent(chan: str | None) -> str | None:
    m = AGENT_CHAN.match(chan or "")
    return m.group(1) if m else None


def _step(e: dict) -> tuple[str, dict] | None:
    """AMI event -> (call_id, step) or None."""
    n, ch = e["event"], e.get("Channel", "")
    cid = e.get("Linkedid") or e.get("call_id")
    caller = e.get("CallerIDNum", "")
    q = e.get("Queue")
    qth = QUEUE_TH.get(q, q)

    def s(kind, frm, to, text, **detail):
        return cid, {"ts": e["ts"], "kind": kind, "frm": frm, "to": to, "text": text, "detail": detail}

    if n == "Newchannel" and e.get("Uniqueid") == e.get("Linkedid"):
        if TRUNK_CHAN.match(ch):
            return s("call_start", "customer", "pjsip", f"สายเข้า 1484 จากลูกค้า {caller}",
                     direction="inbound", caller=caller)
        if _agent(ch):
            return s("call_start", "agent", "pjsip", f"Agent {_agent(ch)} เริ่มโทรออก (click-to-call)",
                     direction="outbound", agent=_agent(ch))
        return None
    if n == "IvrPrompt":
        p = e.get("Prompt", "").strip()
        return s("ivr_prompt", "ivr", "customer", f"IVR เล่นเสียง: {PROMPT_TH.get(p, p)}", prompt=p)
    if n == "DTMFEnd" and e.get("Direction") == "Received" and TRUNK_CHAN.match(ch):
        d = e.get("Digit", "")
        label = IVR_MENU.get(d, {}).get("th", "")
        return s("ivr_digit", "customer", "ivr", f"ลูกค้ากด {d}" + (f" = {label}" if label else ""), digit=d, service=label)
    if n == "QueueCallerJoin":
        return s("queue_join", "ivr", "queue", f"เข้าคิว {qth} (ลำดับที่ {e.get('Position')})", queue=q, position=e.get("Position"))
    if n == "AgentCalled":
        a = (e.get("Interface") or "").split("/")[-1]
        return s("agent_ring", "queue", "agent", f"คิว {qth} เรียก Agent {a}", queue=q, agent=a)
    if n == "AgentRingNoAnswer":
        a = (e.get("Interface") or "").split("/")[-1]
        return s("agent_noanswer", "agent", "queue", f"Agent {a} ไม่รับสาย → ส่งให้ Agent ถัดไป", queue=q, agent=a)
    if n == "AgentConnect":
        a = (e.get("Interface") or "").split("/")[-1]
        return s("agent_answer", "agent", "customer", f"Agent {a} รับสาย (รอคิว {e.get('HoldTime')} วินาที) — เริ่มบันทึกเสียง",
                 queue=q, agent=a, hold_time=e.get("HoldTime"))
    if n == "AgentComplete":
        a = (e.get("Interface") or "").split("/")[-1]
        return s("agent_end", "agent", "cdr", f"Agent {a} จบสาย คุย {e.get('TalkTime')} วินาที", queue=q, agent=a,
                 talk_time=e.get("TalkTime"))
    if n == "QueueCallerAbandon":
        return s("abandon", "customer", "queue", f"ลูกค้าวางสายก่อนมี Agent รับ (คิว {qth})", queue=q)
    if n in ("Hold", "Unhold") and _agent(ch):
        a = _agent(ch)
        return s("hold" if n == "Hold" else "unhold", "agent", "customer",
                 f"Agent {a} พักสาย (Hold)" if n == "Hold" else f"Agent {a} กลับมาคุยต่อ", agent=a)
    if n == "BlindTransfer":
        cid = e.get("TransfereeLinkedid") or e.get("TransfererLinkedid") or cid
        frm = _agent(e.get("TransfererChannel")) or e.get("TransfererCallerIDNum")
        target = e.get("Extension", "")
        is_q = target.startswith("q-")
        label = QUEUE_TH.get(target, target)
        return s("transfer", "agent", "queue" if is_q else "agent",
                 f"Agent {frm} โอนสายไปยัง {'คิว ' + label if is_q else 'Agent ' + target}",
                 from_agent=frm, target=target, target_type="queue" if is_q else "agent", target_label=label)
    if n == "AttendedTransfer":
        cid = e.get("OrigTransfererLinkedid") or cid
        return s("transfer", "agent", "agent", "โอนสาย (Attended)", target=e.get("TransferTargetCallerIDNum"),
                 from_agent=e.get("OrigTransfererCallerIDNum"), target_type="agent", target_label=e.get("TransferTargetCallerIDNum"))
    if n == "DialBegin" and (e.get("DestChannel") or "").startswith("PJSIP/trunk-") and _agent(ch):
        dialed = (e.get("DialString") or "").split("@")[0]
        return s("outbound_dial", "pjsip", "carrier", f"โทรออกไปยัง {dialed} ผ่าน carrier", dialed=dialed, agent=_agent(ch))
    if n == "OutboundDialResult":
        return s("outbound_result", "carrier", "agent", f"ผลการโทรออก {e.get('dialed')}: {e.get('result')}",
                 dialed=e.get("dialed"), result=e.get("result"), dial_status=e.get("dial_status"))
    if n == "Hangup" and e.get("Uniqueid") == e.get("Linkedid"):
        return s("call_end", "pjsip", "cdr", f"จบสาย (สาเหตุ {e.get('Cause-txt') or e.get('Cause')}) — บันทึก CDR / ไฟล์เสียง",
                 cause=e.get("Cause"), cause_text=e.get("Cause-txt"))
    return None


def record(evt: dict) -> None:
    r = _step(evt)
    if not r or not r[0]:
        return
    cid, st = r
    with _lock:
        c = _db()
        c.execute('INSERT INTO steps(call_id, ts, kind, frm, "to", text, detail) VALUES (?,?,?,?,?,?,?)',
                  (cid, st["ts"], st["kind"], st["frm"], st["to"], st["text"], json.dumps(st["detail"], ensure_ascii=False)))
        c.commit()


def _row(r) -> dict:
    return {"id": r[0], "call_id": r[1], "ts": r[2], "kind": r[3], "from": r[4], "to": r[5],
            "from_name": MODULES.get(r[4], r[4]), "to_name": MODULES.get(r[5], r[5]),
            "text": r[6], "detail": json.loads(r[7] or "{}")}


def _secs(a: str, b: str) -> float:
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()
    except ValueError:
        return 0.0


def summarize(call_id: str, steps: list[dict]) -> dict:
    first = next((s for s in steps if s["kind"] == "call_start"), steps[0])
    direction = first["detail"].get("direction") or ("outbound" if any(s["kind"].startswith("outbound") for s in steps) else "inbound")
    queues, agents, transfers, digits = [], [], [], []
    result = "IN_PROGRESS"
    ended = any(s["kind"] == "call_end" for s in steps)
    for s in steps:
        d = s["detail"]
        if s["kind"] == "queue_join" and d.get("queue") not in queues:
            queues.append(d["queue"])
        if s["kind"] == "agent_answer" and d.get("agent") not in agents:
            agents.append(d["agent"])
        if s["kind"] == "ivr_digit":
            digits.append(d.get("digit"))
        if s["kind"] == "transfer":
            transfers.append({"from": d.get("from_agent"), "to": d.get("target"), "to_label": d.get("target_label"),
                              "type": d.get("target_type")})
        if s["kind"] == "outbound_result":
            result = d.get("result") or result
    if direction == "inbound":
        if any(s["kind"] == "abandon" for s in steps):
            result = "ABANDONED"
        elif agents:
            result = "ANSWERED" if ended else "TALKING"
        elif ended:
            result = "NO_AGENT"
    elif ended and result == "IN_PROGRESS":
        result = "UNKNOWN"
    caller = first["detail"].get("caller") or first["detail"].get("agent") or ""
    dialed = next((s["detail"].get("dialed") for s in steps if s["kind"] in ("outbound_dial", "outbound_result")), None)
    return {
        "call_id": call_id, "direction": direction, "caller": caller, "dialed": dialed,
        "start": steps[0]["ts"], "end": steps[-1]["ts"] if ended else None,
        "duration": round(_secs(steps[0]["ts"], steps[-1]["ts"])), "steps": len(steps),
        "ivr_digits": digits, "queues": queues, "agents": agents, "transfers": transfers, "result": result,
    }


def list_calls(limit: int = 50, direction: str | None = None, q: str | None = None) -> list[dict]:
    with _lock:
        c = _db()
        ids = [r[0] for r in c.execute(
            "SELECT call_id FROM steps GROUP BY call_id ORDER BY MIN(id) DESC LIMIT ?", (limit * 4 if (direction or q) else limit,))]
        out = []
        for cid in ids:
            steps = [_row(r) for r in c.execute("SELECT * FROM steps WHERE call_id=? ORDER BY id", (cid,))]
            if not steps:
                continue
            sm = summarize(cid, steps)
            if direction and sm["direction"] != direction:
                continue
            if q and q not in json.dumps(sm, ensure_ascii=False):
                continue
            out.append(sm)
    return out[:limit]


def get_call(call_id: str) -> dict | None:
    with _lock:
        steps = [_row(r) for r in _db().execute("SELECT * FROM steps WHERE call_id=? ORDER BY id", (call_id,))]
    if not steps:
        return None
    t0 = steps[0]["ts"]
    for st in steps:
        st["t"] = round(_secs(t0, st["ts"]), 1)
    return {"summary": summarize(call_id, steps), "steps": steps}
