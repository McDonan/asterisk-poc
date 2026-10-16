"""End-to-end POC test.

Customer Call -> IVR -> Select Service -> Queue -> Agent Answer -> Hold/Transfer -> End Call -> Recording + CDR

The "sim" Asterisk acts as the carrier (customer calls) and as agent phones 1101-1103.
It is driven through ARI (Stasis app "sim"). Everything on the PBX side is verified
only through the integration API (what a CRM / agent app would see).
"""
import asyncio
import inspect
import json
import os
import sys
import time

import httpx
import websockets

API_URL = os.getenv("API_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "poc-api-key")
SIM_ARI = os.getenv("SIM_ARI", "localhost:8089")
ARI_USER, ARI_PASS = "poc", "poc-ari-secret"
AGENTS = ["1101", "1102", "1103"]
QUEUES = ["q-accident", "q-incident", "q-motor", "q-insurance", "q-sales", "q-club"]

api = httpx.AsyncClient(base_url=API_URL, headers={"X-API-Key": API_KEY}, timeout=15)
ari_http = httpx.AsyncClient(base_url=f"http://{SIM_ARI}/ari", auth=(ARI_USER, ARI_PASS), timeout=15)

# ---------------------------------------------------------------- sim state
LOG: list[tuple] = []            # (event, ext/caller, channel_id)
ALIVE: set[str] = set()
AGENT_CH: dict[str, str] = {}    # ext -> current sim channel id
PLAN: dict[str, dict] = {}       # ext -> {"answer": bool}
RESULTS: list[tuple[str, bool, str]] = []
START_TS = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 5))   # PBX CDR clock is UTC


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""), flush=True)


async def wait_until(fn, timeout=20.0, interval=0.5, what=""):
    end = time.time() + timeout
    while time.time() < end:
        r = fn()
        if inspect.isawaitable(r):
            r = await r
        if r:
            return r
        await asyncio.sleep(interval)
    raise AssertionError(f"timeout waiting for {what}")


async def ari(method: str, path: str, **params):
    r = await ari_http.request(method, path, params=params)
    r.raise_for_status()
    return r.json() if r.content else None


async def talk(ch: str):
    """Play speech on the channel so the recording contains audio from both sides."""
    try:
        await ari("POST", f"/channels/{ch}/play", media=["sound:hello-world"] * 30)
    except Exception as exc:
        print(f"  (sim: play on {ch} failed: {exc!r})", flush=True)


# ---------------------------------------------------------------- sim behaviours
async def agent_flow(ch: str, ext: str):
    ALIVE.add(ch)
    AGENT_CH[ext] = ch
    LOG.append(("agent_ring", ext, ch))
    if not PLAN.get(ext, {}).get("answer", True):
        return
    await ari("POST", f"/channels/{ch}/answer")
    LOG.append(("agent_answer", ext, ch))
    asyncio.create_task(talk(ch))


async def ivr_waiting(caller: str) -> bool:
    for c in (await api.get("/calls/active")).json():
        if c["caller"] == caller and (c["application"] or "").lower() in ("background", "waitexten"):
            return True
    return False


async def customer_flow(ch: str, digit: str, caller: str):
    ALIVE.add(ch)
    LOG.append(("customer_answered", caller, ch))
    await wait_until(lambda: ivr_waiting(caller), 25, 0.3, "IVR menu")
    for i, d in enumerate(digit):
        if i:   # after an invalid digit the IVR plays a message first; wait for the menu again
            await asyncio.sleep(1.5)
            await wait_until(lambda: ivr_waiting(caller), 25, 0.3, "IVR menu again")
        await ari("POST", f"/channels/{ch}/dtmf", dtmf=d)
        await asyncio.sleep(0.5)
    LOG.append(("customer_dtmf", caller, digit))
    asyncio.create_task(talk(ch))


async def sim_events(ws):
    async for raw in ws:
        ev = json.loads(raw)
        t = ev.get("type")
        if t == "StasisStart":
            args, ch = ev["args"], ev["channel"]["id"]
            if args and args[0] == "agent":
                asyncio.create_task(agent_flow(ch, args[1]))
            elif args and args[0] == "customer":
                asyncio.create_task(customer_flow(ch, args[1], ev["channel"]["caller"]["number"]))
        elif t in ("ChannelDestroyed", "StasisEnd"):
            ALIVE.discard(ev["channel"]["id"])


# ---------------------------------------------------------------- helpers
_cust_seq = 0


async def place_call(caller: str, digit: str) -> str:
    global _cust_seq
    _cust_seq += 1
    cid = f"cust-{caller}-{_cust_seq}"
    await ari("POST", "/channels", endpoint="PJSIP/1484@pbx", app="sim", appArgs=f"customer,{digit}",
              callerId=caller, channelId=cid, timeout=30)
    return cid


async def hangup(ch: str):
    if ch in ALIVE:
        try:
            await ari("DELETE", f"/channels/{ch}")
        except httpx.HTTPStatusError:
            pass


def new_events(kind: str, since: int):
    return [e for e in LOG[since:] if e[0] == kind]


async def wait_agent_answer(since: int, timeout=40):
    return (await wait_until(lambda: new_events("agent_answer", since), timeout, 0.3, "agent answer"))[0]


async def wait_gone(ch: str, timeout=15):
    await wait_until(lambda: ch not in ALIVE, timeout, 0.3, f"{ch} to end")


async def find_call(caller: str, timeout=30):
    async def f():
        r = [c for c in (await api.get("/calls/history", params={"caller": caller})).json()
             if c["start"] >= START_TS and c["disposition"]]
        return r[0] if r else None
    return await wait_until(f, timeout, 1, f"CDR for {caller}")


async def reset_agents(login: list[str]):
    for ext in AGENTS:
        await api.post(f"/agents/{ext}/logout")
    for ext in login:
        r = await api.post(f"/agents/{ext}/login")
        r.raise_for_status()
        await api.post(f"/agents/{ext}/pause", params={"paused": "false"})


# ---------------------------------------------------------------- scenarios
async def s0_registration():
    print("\n[S0] SIP registration + agent login (feature: SIP/Extension, Agent state)")
    ags = (await api.get("/agents")).json()
    reg = {a["extension"]: a["registered"] for a in ags}
    check("sim agents 1101-1103 registered on PBX", all(reg.get(e) for e in AGENTS), str(reg))
    await reset_agents(AGENTS)
    qs = {q["name"]: len(q["members"]) for q in (await api.get("/queues")).json()}
    check("agents logged in to all 6 queues", all(qs.get(q, 0) >= 3 for q in QUEUES), str(qs))


async def s1_full_flow():
    print("\n[S1] Full flow: IVR(1) -> q-accident -> answer -> hold/unhold -> hangup -> recording + CDR")
    caller, mark = "0811111111", len(LOG)
    cust = await place_call(caller, "1")
    check("inbound call answered by IVR", bool(await wait_until(lambda: new_events("customer_answered", mark), 15, 0.3, "answer")))
    ans = await wait_agent_answer(mark)
    ext, agent_ch = ans[1], ans[2]
    check("IVR DTMF routed call to queue and an agent answered", True, f"agent {ext}")
    await asyncio.sleep(4)

    summary = (await api.get("/monitor/summary")).json()
    qlist = (await api.get("/queues")).json()
    check("monitor: active call + agent in call", summary["active_calls"] >= 1 and summary["agents"]["in_call"] >= 1,
          json.dumps(summary["agents"]))
    check("monitor: queue members visible", any(len(q["members"]) >= 3 for q in qlist))

    await ari("POST", f"/channels/{agent_ch}/hold")
    await asyncio.sleep(3)
    await ari("DELETE", f"/channels/{agent_ch}/hold")
    await asyncio.sleep(2)
    evs = (await api.get("/events", params={"limit": 500})).json()
    names = {e["event"] for e in evs if f"PJSIP/{ext}-" in e.get("Channel", "")}
    check("hold / unhold events observed", {"Hold", "Unhold"} <= names, str(sorted(names & {"Hold", "Unhold"})))

    await hangup(agent_ch)
    await wait_gone(cust)
    call = await find_call(caller)
    check("CDR: ANSWERED, ivr_choice=accident, queue=q-accident", call["disposition"] == "ANSWERED"
          and call["ivr_choice"] == "accident" and call["queue"] == "q-accident", json.dumps(call))
    check("CDR: handled by the agent that answered", call["agent"] == ext, f"cdr agent={call['agent']}")
    recs = (await api.get("/recordings", params={"call_id": call["call_id"]})).json()
    ok = bool(recs)
    size = 0
    if ok:
        data = (await api.get(f"/recordings/{recs[0]['path']}")).content
        size = len(data)
        ok = data[:4] == b"RIFF" and size > 16000 * 2
    check("recording exists, downloadable, valid WAV", ok, f"{recs[0]['path'] if recs else '-'} {size} bytes")
    crm = (await api.get("/mock-crm/screenpop")).json()
    check("CRM webhook (screen pop) received", any(c["call_id"] == call["call_id"] and c["caller_number"] == caller for c in crm))
    ql = (await api.get("/queue-log", params={"queue": "q-accident"})).json()
    check("queue_log has CONNECT + COMPLETE*", {"CONNECT"} <= {r["event"] for r in ql}
          and any(r["event"].startswith("COMPLETE") for r in ql))


async def s2_distribution():
    print("\n[S2] Queue distribution: 3 sequential calls should reach 3 different agents")
    got = []
    for i in range(3):
        caller, mark = f"082222222{i}", len(LOG)
        cust = await place_call(caller, "1")
        ans = await wait_agent_answer(mark)
        got.append(ans[1])
        await asyncio.sleep(3)
        await hangup(ans[2])
        await wait_gone(cust)
    check("calls distributed across agents (leastrecent)", len(set(got)) == 3, str(got))
    await asyncio.sleep(6)  # wrapuptime


async def s3_transfer():
    print("\n[S3] Transfer: agent -> other agent (API), then agent -> queue (API)")
    caller, mark = "0833333333", len(LOG)
    cust = await place_call(caller, "2")   # 2 = other incidents (q-incident)
    a = await wait_agent_answer(mark)
    await asyncio.sleep(3)
    target = next(e for e in AGENTS if e != a[1])
    mark2 = len(LOG)
    r = await api.post(f"/agents/{a[1]}/transfer", json={"target": target})
    check("API blind transfer accepted", r.status_code == 200, r.text[:120])
    b = (await wait_until(lambda: [e for e in new_events("agent_answer", mark2) if e[1] == target], 30, 0.3, "target agent"))[0]
    check("transfer target agent answered", True, f"{a[1]} -> {b[1]}")
    await wait_until(lambda: a[2] not in ALIVE, 10, 0.3, "first agent leg released")
    check("original agent released after transfer", True)
    await asyncio.sleep(3)

    # second hop: agent -> queue (q-motor)
    mark3 = len(LOG)
    r = await api.post(f"/agents/{target}/transfer", json={"target": "q-motor"})
    check("API transfer to queue accepted", r.status_code == 200, r.text[:120])
    c = await wait_agent_answer(mark3)
    check("queue re-routed call to an agent", True, f"agent {c[1]}")
    await asyncio.sleep(3)
    r = await api.post(f"/agents/{c[1]}/hangup")
    check("API agent hang-up", r.status_code == 200, r.text[:120])
    await wait_gone(cust)
    call = await find_call(caller)
    recs = (await api.get("/recordings", params={"call_id": call["call_id"]})).json()
    check("single recording covers the whole transferred call", len(recs) == 1, f"{len(recs)} file(s), legs={call['legs']}")
    await asyncio.sleep(6)


async def s4_abandon_and_noagent():
    print("\n[S4] Abandon (customer hangs up while ringing) + no agent available")
    PLAN.update({e: {"answer": False} for e in AGENTS})
    caller, mark = "0844444444", len(LOG)
    cust = await place_call(caller, "1")
    await wait_until(lambda: new_events("agent_ring", mark), 30, 0.3, "agent ring")
    await asyncio.sleep(2)
    await hangup(cust)
    await asyncio.sleep(3)
    ql = (await api.get("/queue-log", params={"queue": "q-accident", "event": "ABANDON"})).json()
    check("queue_log ABANDON recorded", bool(ql))
    PLAN.clear()
    for e in AGENTS:  # clean up ringing sim legs
        if AGENT_CH.get(e):
            await hangup(AGENT_CH[e])
    await asyncio.sleep(2)

    await reset_agents([])
    caller, mark = "0855555555", len(LOG)
    cust = await place_call(caller, "3")
    await wait_gone(cust, 40)
    call = await find_call(caller)
    check("no logged-in agent: call rejected after message, no agent leg", call["agent"] is None, json.dumps(call))
    await reset_agents(AGENTS)


async def s5_click_to_call():
    print("\n[S5] API click-to-call: ring agent 1101, then connect to agent 1102")
    mark = len(LOG)
    r = await api.post("/calls/originate", json={"agent": "1101", "destination": "1102"})
    check("originate accepted", r.status_code == 200, r.text[:120])
    await wait_until(lambda: [e for e in new_events("agent_answer", mark) if e[1] == "1101"], 20, 0.3, "1101 answer")
    b = await wait_until(lambda: [e for e in new_events("agent_ring", mark) if e[1] == "1102"], 20, 0.3, "1102 ring")
    check("destination extension rang", bool(b))
    for e in ("1101", "1102"):
        if AGENT_CH.get(e):
            await hangup(AGENT_CH[e])
    await asyncio.sleep(1)


async def s6_ivr_invalid():
    print("\n[S6] IVR invalid option then valid option")
    caller, mark = "0866666666", len(LOG)
    cust = await place_call(caller, "71")   # 7 = invalid -> menu again -> 1 (accident)
    ans = await wait_agent_answer(mark, 60)
    check("invalid DTMF handled, retry reached queue", True, f"agent {ans[1]}")
    await asyncio.sleep(2)
    await hangup(ans[2])
    await wait_gone(cust)


async def outbound_call(number: str, agent: str = "1101") -> tuple[int, str]:
    """Click-to-call: PBX rings the sim agent, the agent answers, PBX dials 9+number via the sim carrier."""
    mark = len(LOG)
    r = await api.post("/calls/originate", json={"agent": agent, "destination": number})
    r.raise_for_status()
    ans = await wait_until(lambda: [e for e in new_events("agent_answer", mark) if e[1] == agent], 20, 0.3,
                           f"agent {agent} answer")
    return mark, ans[0][2]


async def outbound_record(number: str, timeout=25):
    async def f():
        r = [x for x in (await api.get("/calls/outbound", params={"dialed": number})).json()
             if x["start"] >= START_TS and x["disposition"]]
        return r[0] if r else None
    return await wait_until(f, timeout, 1, f"outbound CDR for {number}")


async def s7_outbound():
    print("\n[S7] Outbound: click-to-call -> carrier outcome (connected / busy / no answer / failed) + recording status")
    await reset_agents(AGENTS)
    base = "090000000"

    # 1: carrier answers, parties talk, agent hangs up
    n = base + "1"
    _, ch = await outbound_call(n)
    await asyncio.sleep(6)
    await hangup(ch)
    await wait_gone(ch)
    c = await outbound_record(n)
    check("outbound answered: result=CONNECTED, direction=outbound, agent=1101",
          c["result"] == "CONNECTED" and c["direction"] == "outbound" and c["agent"] == "1101", json.dumps(c))
    check("outbound answered: talk time > 0 and recording_status=recorded",
          c["talk_seconds"] > 0 and c["recording_status"] == "recorded", f"talk={c['talk_seconds']} rec={c['recording']}")
    recs = (await api.get("/recordings", params={"call_id": c["call_id"]})).json()
    size, ok = 0, False
    if recs:
        data = (await api.get(f"/recordings/{recs[0]['path']}")).content
        size = len(data)
        ok = data[:4] == b"RIFF" and size > 16000 * 3
    check("outbound recording downloadable, valid WAV", ok, f"{size} bytes")
    evs = (await api.get("/events", params={"event": "OutboundDialResult", "limit": 50})).json()
    check("real-time event OutboundDialResult (CONNECTED)",
          any(e["dialed"] == n and e["result"] == "CONNECTED" for e in evs), str(evs[-1:]))
    await asyncio.sleep(1)

    # 6: carrier answers then remote party hangs up
    n = base + "6"
    _, ch = await outbound_call(n)
    await wait_gone(ch, 25)
    c = await outbound_record(n)
    check("remote hangup: CONNECTED and agent leg released by PBX", c["result"] == "CONNECTED", json.dumps(c))
    check("remote hangup: recording_status=recorded", c["recording_status"] == "recorded")
    await asyncio.sleep(1)

    # not connected cases
    for digit, want, cause_note in ("2", "BUSY", "busy"), ("4", "FAILED", "congestion"), ("5", "FAILED", "unallocated"):
        n = base + digit
        _, ch = await outbound_call(n)
        await wait_gone(ch, 25)
        c = await outbound_record(n)
        check(f"outbound {cause_note}: result={want}, no connection",
              c["result"] == want and c["talk_seconds"] == 0, f"{c['result']} dial_status={c['dial_status']} cause={c['hangup_cause']}")
        check(f"outbound {cause_note}: recording_status=not_recorded", c["recording_status"] == "not_recorded")
        await asyncio.sleep(1)

    # 3: no answer (PBX dial timeout 20 s)
    n = base + "3"
    _, ch = await outbound_call(n)
    await wait_gone(ch, 40)
    c = await outbound_record(n)
    check("outbound no answer: result=NO_ANSWER after ~20 s ring",
          c["result"] == "NO_ANSWER" and c["ring_seconds"] >= 15, f"{c['result']} ring={c['ring_seconds']}")
    check("outbound no answer: recording_status=not_recorded", c["recording_status"] == "not_recorded")
    await asyncio.sleep(1)

    # 3 again: agent gives up while the far end is still ringing -> CANCELLED
    n = base + "3"
    before = {x["call_id"] for x in (await api.get("/calls/outbound", params={"dialed": n})).json()}
    _, ch = await outbound_call(n)
    await asyncio.sleep(5)
    await hangup(ch)
    await wait_gone(ch)

    async def fresh():
        r = [x for x in (await api.get("/calls/outbound", params={"dialed": n})).json() if x["call_id"] not in before]
        return r[0] if r and r[0]["disposition"] else None
    c = await wait_until(fresh, 25, 1, "cancelled outbound CDR")
    check("outbound cancelled by agent while ringing: result=CANCELLED", c["result"] == "CANCELLED",
          f"{c['result']} dial_status={c['dial_status']}")

    # filters
    res = (await api.get("/calls/outbound", params={"result": "BUSY"})).json()
    check("API filter /calls/outbound?result=BUSY", len(res) >= 1 and all(x["result"] == "BUSY" for x in res))
    summary = {x["result"] for x in (await api.get("/calls/history", params={"direction": "outbound"})).json()}
    check("history?direction=outbound lists every result type",
          {"CONNECTED", "BUSY", "NO_ANSWER", "FAILED", "CANCELLED"} <= summary, str(sorted(summary)))


async def s8_transactions():
    print("\n[S8] Transaction view: IVR digit, queues, agents and transfer hops per call")
    rows = (await api.get("/transactions", params={"q": "0833333333", "limit": 5})).json()
    row = rows[0] if rows else {}
    check("transactions: call with IVR digit 2 found", row.get("ivr_digits") == ["2"], json.dumps(row)[:200])
    check("transactions: transfer recorded (agent -> q-motor)",
          any(t.get("to") == "q-motor" for t in row.get("transfers", [])), json.dumps(row.get("transfers")))
    detail = (await api.get(f"/transactions/{row['call_id']}")).json() if row else {"steps": []}
    kinds = [s["kind"] for s in detail["steps"]]
    check("transaction steps cover IVR -> queue -> agent -> transfer -> end",
          all(k in kinds for k in ("call_start", "ivr_prompt", "ivr_digit", "queue_join", "agent_answer", "transfer", "call_end")),
          ",".join(dict.fromkeys(kinds)))
    check("transaction steps name from/to modules", all(s.get("from") and s.get("to") for s in detail["steps"]))
    check("transaction detail joins the CDR row (recording)", bool((detail.get("cdr") or {}).get("recording")))


async def s9_agents_admin():
    print("\n[S9] Add / remove agents at runtime")
    r = await api.post("/demo/agents", json={"name": "E2E Agent", "login": True})
    check("agent added", r.status_code == 200, r.text[:120])
    ext = r.json()["extension"]
    qs = {q["name"]: [m["interface"] for m in q["members"]] for q in (await api.get("/queues")).json()}
    check("new agent is a member of all 6 queues", all(f"PJSIP/{ext}" in qs.get(q, []) for q in QUEUES), ext)
    reg = (await api.get("/demo/agents/registry")).json()
    check("agent present in registry", any(a["extension"] == ext for a in reg), str(reg)[:120])
    r = await api.delete(f"/demo/agents/{ext}")
    check("agent removed", r.status_code == 200, r.text[:120])
    qs = (await api.get("/queues")).json()
    check("removed agent no longer in any queue", not any(ext in json.dumps(q["members"]) for q in qs))
    reg = (await api.get("/demo/agents/registry")).json()
    check("agent gone from registry", not any(a["extension"] == ext for a in reg))


async def s10_concurrent():
    print("\n[S10] Concurrent customers: 3 simultaneous calls -> 3 different agents")
    await reset_agents(AGENTS)
    await asyncio.sleep(1)
    mark = len(LOG)
    custs = await asyncio.gather(*[place_call(f"087700000{i}", d) for i, d in enumerate("135", 1)])
    await wait_until(lambda: len(new_events("agent_answer", mark)) >= 3, 60, 0.3, "3 agent answers")
    got = new_events("agent_answer", mark)
    check("3 concurrent calls answered by 3 different agents", len({g[1] for g in got}) == 3, str([g[1] for g in got]))
    active = (await api.get("/calls/active")).json()
    check("monitor shows 3 simultaneous calls", len(active) >= 3, f"{len(active)} active")
    await asyncio.sleep(3)   # an agent hang-up before the bridge is established would put the caller back in the queue
    for g in got:
        await hangup(g[2])
    for c in custs:
        await wait_gone(c)
    await asyncio.sleep(6)


async def main() -> int:
    # wait for API + AMI
    async def ready():
        try:
            return (await api.get("/health")).json().get("ami_connected")
        except Exception:
            return False
    await wait_until(ready, 90, 2, "API/AMI ready")
    await api.post("/demo/disable")      # only one ARI client may serve Stasis app "sim"

    async with websockets.connect(
        f"ws://{SIM_ARI}/ari/events?app=sim&api_key={ARI_USER}:{ARI_PASS}") as ws:
        loop_task = asyncio.create_task(sim_events(ws))
        try:
            async def regd():
                ags = (await api.get("/agents")).json()
                return all(a["registered"] for a in ags if a["extension"] in AGENTS) and len(ags) >= 3
            await wait_until(regd, 60, 2, "sim agent registration")
            only = {x.strip() for x in os.getenv("ONLY", "").split(",") if x.strip()}   # e.g. ONLY=s0,s1
            for sc in (s0_registration, s1_full_flow, s2_distribution, s3_transfer,
                       s4_abandon_and_noagent, s5_click_to_call, s6_ivr_invalid, s7_outbound,
                       s8_transactions, s9_agents_admin, s10_concurrent):
                if only and sc.__name__.split("_")[0] not in only and sc is not s0_registration:
                    continue
                try:
                    await sc()
                except Exception as exc:  # keep going, report
                    check(f"{sc.__name__} aborted", False, repr(exc))
        finally:
            loop_task.cancel()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n==== {len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed ====")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
