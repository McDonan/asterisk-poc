"""CDR (sqlite), call recordings (filesystem) and queue_log readers."""
import os
import re
import sqlite3
from pathlib import Path

from . import config


# ---------------- CDR ----------------
def _cdr_conn() -> sqlite3.Connection:
    if not os.path.exists(config.CDR_DB):
        raise FileNotFoundError("CDR database not created yet (no call has finished)")
    conn = sqlite3.connect(f"file:{config.CDR_DB}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def query_cdr(src=None, dst=None, linkedid=None, date_from=None, date_to=None,
              limit=100, offset=0) -> list[dict]:
    where, args = [], []
    if src:
        where.append("src = ?"); args.append(src)
    if dst:
        where.append("dst = ?"); args.append(dst)
    if linkedid:
        where.append("linkedid = ?"); args.append(linkedid)
    if date_from:
        where.append("calldate >= ?"); args.append(date_from)
    if date_to:
        where.append("calldate <= ?"); args.append(date_to)
    sql = "SELECT * FROM cdr"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY calldate DESC, sequence DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    with _cdr_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def _num(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


_DIAL_RESULT = {"ANSWER": "CONNECTED", "BUSY": "BUSY", "NOANSWER": "NO_ANSWER", "CANCEL": "CANCELLED"}

# Q.850 cause codes most relevant for call-center outbound reporting
HANGUP_CAUSE_TEXT = {
    "0": "not defined", "1": "unallocated number", "16": "normal clearing", "17": "user busy",
    "18": "no user responding", "19": "no answer", "20": "subscriber absent", "21": "call rejected",
    "27": "destination out of order", "28": "invalid number format", "34": "no circuit available",
    "38": "network out of order", "41": "temporary failure", "42": "switch congestion",
}


def _outbound_result(dial_status: str | None, disposition: str | None) -> str:
    if dial_status in _DIAL_RESULT:
        return _DIAL_RESULT[dial_status]
    if dial_status:                      # CONGESTION, CHANUNAVAIL, DONTCALL, INVALID, ...
        return "FAILED"
    return "CONNECTED" if disposition == "ANSWERED" else "UNKNOWN"


def _recording_status(rel_path: str | None) -> str:
    """recorded only if the file really exists: MixMonitor(b) writes nothing for calls never bridged."""
    if not rel_path:
        return "not_recorded"
    p = Path(config.REC_DIR) / rel_path
    try:
        return "recorded" if p.is_file() and p.stat().st_size > 44 else "not_recorded"
    except OSError:
        return "not_recorded"


def call_history(limit=50, caller=None, date_from=None, date_to=None,
                 direction=None, agent=None, result=None, dialed=None) -> list[dict]:
    """Group CDR legs by linkedid into one row per call (inbound customer call or outbound agent call)."""
    where, args = [], []
    if date_from:
        where.append("calldate >= ?"); args.append(date_from)
    if date_to:
        where.append("calldate <= ?"); args.append(date_to)
    sql = "SELECT * FROM cdr"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY calldate ASC, sequence ASC"
    with _cdr_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, args)]

    calls: dict[str, dict] = {}
    for r in rows:
        c = calls.setdefault(r["linkedid"], {
            "call_id": r["linkedid"], "start": r["calldate"], "caller": r["src"],
            "direction": None, "ivr_choice": None, "queue": None, "agent": None, "agents": [],
            "dialed": None, "dial_status": None, "hangup_cause": None,
            "total_seconds": 0, "ring_seconds": 0, "talk_seconds": 0, "disposition": None,
            "recording": None, "legs": 0, "rating": None,
        })
        if r.get("rating") not in (None, ""):
            c["rating"] = _num(r["rating"])
        c["legs"] += 1
        c["direction"] = c["direction"] or r.get("direction") or None
        c["ivr_choice"] = c["ivr_choice"] or r.get("ivr_choice") or None
        c["recording"] = c["recording"] or r.get("recording") or None
        c["dialed"] = c["dialed"] or r.get("dialed") or None
        c["dial_status"] = r.get("dial_status") or c["dial_status"]
        c["hangup_cause"] = r.get("hangup_cause") or c["hangup_cause"]
        if r.get("dial_seconds") not in (None, ""):
            c["_dial"], c["_answered"] = _num(r["dial_seconds"]), _num(r.get("answered_seconds"))
        if r.get("agent"):
            c["agent"] = r["agent"]
            if r["agent"] not in c["agents"]:
                c["agents"].append(r["agent"])
        c["total_seconds"] = max(c["total_seconds"], _num(r["duration"]))
        if r["lastapp"] == "Queue" and r["lastdata"]:
            c["queue"] = r["lastdata"].split(",")[0]
        dst = r.get("dstchannel") or ""
        m = re.match(r"PJSIP/(\d+)-", dst)
        if m and r["disposition"] == "ANSWERED":
            c["agent"] = m.group(1)
            if m.group(1) not in c["agents"]:
                c["agents"].append(m.group(1))
            c["talk_seconds"] += _num(r["billsec"])
        if dst.startswith("PJSIP/trunk-sim-"):          # outbound leg towards the carrier
            c["ring_seconds"] = max(c["ring_seconds"], _num(r["duration"]) - _num(r["billsec"]))
            c["talk_seconds"] += _num(r["billsec"]) if r["disposition"] == "ANSWERED" else 0
        if r["disposition"] == "ANSWERED":
            c["disposition"] = "ANSWERED"
        else:
            c["disposition"] = c["disposition"] or r["disposition"]

    out = []
    for c in sorted(calls.values(), key=lambda x: x["start"], reverse=True):
        if "_dial" in c:                  # outbound: precise ring / talk time measured by Dial()
            c["ring_seconds"] = max(c["_dial"] - c["_answered"], 0)
            c["talk_seconds"] = c["_answered"]
        c.pop("_dial", None); c.pop("_answered", None)
        if c["direction"] == "outbound":
            c["caller"] = c["agent"] or c["caller"]
            c["result"] = _outbound_result(c["dial_status"], c["disposition"])
            c["hangup_cause_text"] = HANGUP_CAUSE_TEXT.get(str(c["hangup_cause"] or ""))
        else:
            c["result"] = None
            c["hangup_cause_text"] = None
        c["recording_status"] = _recording_status(c["recording"])
        if direction and c["direction"] != direction:
            continue
        if caller and c["caller"] != caller:
            continue
        if agent and agent not in c["agents"]:
            continue
        if result and c["result"] != result:
            continue
        if dialed and c["dialed"] != dialed:
            continue
        out.append(c)
    return out[:limit]


# ---------------- Recordings ----------------
_REC_RE = re.compile(r"(?P<linkedid>[\d.]+)-(?P<caller>[^.]+)\.wav$")


def list_recordings(date: str | None = None, caller: str | None = None, linkedid: str | None = None):
    base = Path(config.REC_DIR)
    if not base.exists():
        return []
    root = base / date if date else base          # date as YYYY/MM/DD
    root = root.resolve()
    if base.resolve() not in root.parents and root != base.resolve():
        raise ValueError("invalid date")
    items = []
    for p in root.rglob("*.wav"):
        m = _REC_RE.match(p.name)
        info = m.groupdict() if m else {"linkedid": None, "caller": None}
        if caller and info["caller"] != caller:
            continue
        if linkedid and info["linkedid"] != linkedid:
            continue
        st = p.stat()
        items.append({
            "path": str(p.relative_to(base)), "call_id": info["linkedid"], "caller": info["caller"],
            "size_bytes": st.st_size, "modified": st.st_mtime,
        })
    return sorted(items, key=lambda x: x["modified"], reverse=True)


def resolve_recording(rel_path: str) -> Path:
    base = Path(config.REC_DIR).resolve()
    p = (base / rel_path).resolve()
    if base not in p.parents or p.suffix != ".wav" or not p.is_file():
        raise FileNotFoundError(rel_path)
    return p


# ---------------- queue_log ----------------
QUEUE_LOG_FIELDS = ["epoch", "call_id", "queue", "agent", "event", "d1", "d2", "d3", "d4", "d5"]


def read_queue_log(limit=200, queue=None, event=None) -> list[dict]:
    if not os.path.exists(config.QUEUE_LOG):
        return []
    with open(config.QUEUE_LOG, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    out = []
    for line in reversed(lines):
        parts = line.rstrip("\n").split("|")
        rec = dict(zip(QUEUE_LOG_FIELDS, parts))
        if queue and rec.get("queue") != queue:
            continue
        if event and rec.get("event") != event:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out
