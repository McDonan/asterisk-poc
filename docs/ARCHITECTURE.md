# Architecture Diagrams: Implemented vs. To Build

Companion to [CALL-CENTER.md](CALL-CENTER.md) (concepts, Thai) and [POC-REPORT.md](POC-REPORT.md) (evaluation).

Legend: **green** = implemented in this POC, **yellow** = simulated in the POC and must be replaced or set up for production, **red** = not built.

## 1. System layers

```mermaid
flowchart TB
    subgraph L5["5. Business apps"]
        CRM["CRM / Agent Desktop"]:::todo
        DASH["Supervisor dashboard / reports"]:::todo
        DEMO["Exec demo page<br/>api/app/static/demo.html"]:::done
        MOCK["Mock CRM screen-pop"]:::sim
    end
    subgraph L4["4. Integration layer"]
        API["FastAPI: REST + WebSocket<br/>api/app/main.py, ami.py"]:::done
    end
    subgraph L3["3. Telephony core: Asterisk"]
        PBX["PJSIP, dialplan, queues,<br/>MixMonitor, CDR<br/>asterisk/etc/*.conf"]:::done
    end
    subgraph L2["2. Endpoints"]
        SOFT["Agent softphones 1001-1003"]:::sim
        SIMAG["Simulated agents 1101-1103"]:::sim
        WEBRTC["Browser phone (WebRTC)"]:::todo
    end
    subgraph L1["1. Outside world"]
        SIMCAR["Simulated carrier + customers<br/>sim/etc"]:::sim
        TRUNK["Real SIP trunk from a carrier"]:::todo
        CUST["Real customers (PSTN)"]:::todo
    end
    CRM --> API
    DASH --> API
    DEMO --> API
    API -- AMI --> PBX
    API -- webhook --> MOCK
    PBX --- SOFT
    PBX --- SIMAG
    PBX --- WEBRTC
    PBX --- SIMCAR
    PBX --- TRUNK
    TRUNK --- CUST
    classDef done fill:#d4f4dd,stroke:#2e7d32,color:#000
    classDef sim fill:#fff3cd,stroke:#b58900,color:#000
    classDef todo fill:#f8d7da,stroke:#c62828,color:#000
```

## 2. Dialplan map: where each call goes

Core of [asterisk/etc/extensions.conf](../asterisk/etc/extensions.conf). Each box is a context (a named group of rules).

```mermaid
flowchart LR
    IN(["Inbound call<br/>from the trunk"]) --> FT["from-trunk<br/>DID 8000"]
    FT --> IVR["ivr-main<br/>Thai welcome, wait for DTMF<br/>25 s x 3 tries"]
    IVR -- "1" --> QC["queue-flow: q-claim"]
    IVR -- "2" --> QP["queue-flow: q-policy"]
    IVR -- "0" --> QG["queue-flow: q-general"]
    IVR -- "9" --> IVR
    IVR -- "other key (i)" --> IVR
    QC --> REC["sub-record<br/>MixMonitor 'b'<br/>CDR(recording)"]
    QP --> REC
    QG --> REC
    REC --> AG["Queue(): MOH while waiting,<br/>ring Agent (leastrecent)"]
    AG --> TALK(["Agent talks<br/>Hold / Transfer / Hangup"])
    AG -. "no agent" .-> NOAG["th-noagent, hangup"]

    OUT(["Agent dials 9 + number<br/>or API click-to-call"]) --> FI["from-internal"]
    FI --> SO["sub-outbound<br/>CDR direction=outbound<br/>MixMonitor 'b'"]
    SO --> TR["Dial trunk-sim<br/>timeout 20 s"]
    TR --> HH["sub-outbound-hangup<br/>CDR: dial_status, hangup_cause"]
```

Also in `from-internal`: `*45` / `*46` / `*47` / `*48` = queue login / logout / pause / unpause, `600` / `601` = echo and test tone, `1XXX` = agent to agent.

## 3. Sequence: one inbound call end to end

```mermaid
sequenceDiagram
    autonumber
    actor Cu as Customer
    participant Ca as Carrier (sim)
    participant P as Asterisk PBX
    participant Ag as Agent
    participant A as API (AMI)
    participant C as CRM
    Cu->>Ca: dials 8000
    Ca->>P: SIP INVITE (trunk)
    P-->>Cu: Thai IVR prompt (RTP)
    Cu->>P: DTMF "1"
    P->>P: queue q-claim, start MixMonitor
    P-->>Cu: Music on Hold
    P->>Ag: ring (leastrecent member)
    Ag->>P: answer
    P-->>A: AMI events (QueueCallerJoin, AgentConnect, ...)
    A->>C: webhook screen-pop
    Note over Cu,Ag: conversation, recorded to a WAV file
    Ag->>P: Hold / blind transfer (via API or softphone)
    Cu--xP: hangup
    P->>P: write CDR + recording path
    P-->>A: Cdr event
    A-->>A: build "call history" (IVR, queue, agent, wait/talk, result)
```

## 4. Sequence: click-to-call (outbound)

```mermaid
sequenceDiagram
    autonumber
    participant U as CRM / Agent Desktop
    participant A as API
    participant P as Asterisk PBX
    actor Ag as Agent phone
    participant Ca as Carrier
    actor Cu as Customer
    U->>A: POST /calls/originate {agent, destination}
    A->>P: AMI Originate to agent (leg 1)
    P->>Ag: ring
    Ag->>P: answer
    P->>Ca: Dial trunk (leg 2)
    Ca->>Cu: ring
    alt customer answers
        Cu->>Ca: answer
        Ca->>P: 200 OK
        P->>P: bridge both legs, record
        Note over Ag,Cu: result = CONNECTED, recording_status = recorded
    else busy / no answer / failed
        Ca-->>P: 486 / timeout 20 s / 503
        Note over P: result = BUSY, NO_ANSWER or FAILED,<br/>not_recorded
    end
    P-->>A: event OutboundDialResult + CDR
```

## 5. What the demo page does

```mermaid
flowchart LR
    B["Presenter clicks<br/>in the browser"] --> D["api/app/demo.py<br/>(ARI client)"]
    D -- "ARI: originate, DTMF,<br/>answer, hold, hangup" --> S["sim Asterisk<br/>fake carrier + customers<br/>+ agents 1101-1103"]
    S -- "SIP/RTP" --> P["Real PBX<br/>IVR, queue, recording, CDR"]
    P -- "AMI events" --> A["API state<br/>(calls, KPI, history)"]
    A --> B
    classDef sim fill:#fff3cd,stroke:#b58900,color:#000
    classDef done fill:#d4f4dd,stroke:#2e7d32,color:#000
    class S,D sim
    class P,A done
```

Only the people are fake (customer, carrier, agents). The PBX behaviour shown (IVR, queue, hold, transfer, CDR, events) comes from the real Asterisk.

## 6. Checklist

### A. Implemented in this POC

| Capability | Where |
|---|---|
| SIP endpoints, trunk, NAT/RTP settings | [pjsip.conf](../asterisk/etc/pjsip.conf), [rtp.conf](../asterisk/etc/rtp.conf) |
| IVR (Thai prompts, DTMF, retry/invalid/repeat) | `ivr-main` in [extensions.conf](../asterisk/etc/extensions.conf) |
| Queues (3 skills, leastrecent) and hold music | [queues.conf](../asterisk/etc/queues.conf), [musiconhold.conf](../asterisk/etc/musiconhold.conf) |
| Recording (inbound and outbound) | `sub-record` + `MixMonitor(b)` |
| CDR with custom fields (IVR choice, agent, recording, dial status) | [cdr_sqlite3_custom.conf](../asterisk/etc/cdr_sqlite3_custom.conf) |
| Outbound with result classification | `sub-outbound`, `sub-outbound-hangup` |
| API: agents, queues, active calls, history, CDR, recordings, events (REST + WebSocket) | [api/app](../api/app) |
| Agent actions via API: login, logout, pause, transfer, hangup, click-to-call | [main.py](../api/app/main.py) |
| CRM screen-pop webhook (mock) | `/mock-crm/screenpop` |
| Exec demo page | [demo.html](../api/app/static/demo.html), [demo.py](../api/app/demo.py) |
| Automated tests (42 checks, scenarios S0 to S7) | [tests/e2e.py](../tests/e2e.py) |

### B. Simulated now: create or configure for production

| Item | What to do |
|---|---|
| SIP trunk | Replace `trunk-sim` with the carrier's trunk in `pjsip.conf`: credentials or IP auth, codecs, DID mapping in `from-trunk`, outbound prefix and caller ID rules. |
| Agent phones | Decide: softphone, desk phone or WebRTC. Create real endpoints and credentials per agent. |
| Network | Public IP/NAT (`media_address`, `external_*`), firewall for 5060 and the RTP range, SBC if needed. |
| Security | Change all lab passwords, use TLS/SRTP, lock down AMI/ARI, real API authentication (JWT/SSO). |
| Database | CDR is SQLite on a volume. Move to PostgreSQL/MySQL with backups. |
| Recording storage | Shared or object storage, retention, encryption, access audit, PDPA rules. |
| CRM integration | Replace the mock webhook with the real CRM API. |
| Prompts and hold music | Professional Thai prompts and real MOH (current ones are POC-generated). |
| Business rules | Real queue strategy, SLA, business hours, holiday routing, overflow and timeout behaviour. |
| Transfer rules | `POST /agents/{ext}/transfer` does a direct blind transfer and ignores agent pause state. Add a state check or transfer through queues. |

### C. Not built (gaps for the report)

| Gap | Note |
|---|---|
| Agent Desktop UI (browser softphone, wrap-up codes) | Frontend work, typically WebRTC with JsSIP or SIP.js. |
| Supervisor tools: listen, whisper, barge | Asterisk supports `ChanSpy`; UI not built. |
| Historical reports (SLA, ASA, AHT, abandon rate) | Data exists in CDR and queue_log; dashboards not built. |
| Auto/predictive dialer, campaigns, call lists, DNC | Out of scope of the POC. |
| High availability, load test, monitoring and alerting | Not tested. |
| User and role management, WFM, QA scoring | Not built. |

## 7. Production reference architecture

A reference design, not a sized plan. Service counts are typical starting points and must be confirmed by a load test.

### 7.1 Logical architecture and how the services talk

```mermaid
flowchart TB
    subgraph EXT["Outside"]
        PSTN["Customers via PSTN"]
        CARRIER["Carrier SIP trunks (2 for redundancy)"]
        BROWSER["Agent / supervisor browsers"]
        CRMX["Corporate CRM"]
    end

    subgraph EDGE["Edge zone (DMZ)"]
        SBC["SBC / SIP proxy: Kamailio<br/>x2"]
        RTPE["Media relay: RTPengine<br/>x2"]
        NGINX["Reverse proxy / WAF: Nginx<br/>x2 (TLS, WSS)"]
    end

    subgraph CORE["Telephony zone"]
        AST["Asterisk PBX nodes<br/>IVR, queues, recording, WebRTC<br/>x2 (active + standby)"]
        TGW["Telephony gateway<br/>AMI/ARI client, x2 (leader election)"]
    end

    subgraph APP["Application zone"]
        BUS[("Message bus<br/>Redis Streams / NATS")]
        API["Call-center API<br/>REST + WebSocket, x2+"]
        REC["Recording processor<br/>encrypt, convert, upload, x2"]
        CTI["CRM adapter<br/>screen-pop, call logging, x1-2"]
        AUTH["Identity provider (SSO)<br/>Keycloak, x2"]
        WEB["Agent Desktop + Supervisor UI<br/>static web app"]
    end

    subgraph DATA["Data zone"]
        PG[("PostgreSQL<br/>CDR, queue log, call history, config<br/>primary + replica")]
        S3[("Object storage<br/>recordings (S3 / MinIO)")]
        VAULT["Secrets: Vault"]
    end

    subgraph OPS["Operations"]
        OBS["Prometheus, Grafana, Loki, alerts"]
        HEP["SIP capture: Homer / sngrep"]
        CICD["CI/CD + config management"]
    end

    PSTN --- CARRIER
    CARRIER -- "SIP/TLS + RTP/SRTP" --> SBC
    SBC -- "SIP" --> AST
    CARRIER -. "RTP" .-> RTPE
    RTPE -. "RTP" .-> AST
    BROWSER -- "HTTPS" --> NGINX
    BROWSER -- "WSS: SIP over WebSocket" --> NGINX
    NGINX -- "HTTP" --> API
    NGINX -- "WSS" --> AST
    NGINX --> WEB
    BROWSER -. "SRTP media" .-> RTPE
    AST -- "AMI / ARI events" --> TGW
    TGW -- "publish events" --> BUS
    API -- "commands: originate, transfer, pause" --> TGW
    BUS --> API
    BUS --> CTI
    CTI -- "REST / webhook" --> CRMX
    AST -- "ODBC: CDR, queue_log" --> PG
    AST -- "recordings spool" --> REC
    REC --> S3
    REC -- "metadata" --> PG
    API --> PG
    API -- "presigned URL" --> S3
    API -- "OIDC / JWT" --> AUTH
    AST --> OBS
    API --> OBS
    SBC --> HEP
    VAULT -. "credentials" .-> AST
    VAULT -. "credentials" .-> API
```

### 7.2 Three kinds of traffic

```mermaid
flowchart LR
    subgraph SIG["Signaling: setup the call"]
        direction LR
        S1["Carrier / agent phone"] -- "SIP (UDP/TLS 5060/5061)<br/>WSS for browsers" --> S2["SBC"] --> S3["Asterisk"]
    end
    subgraph MED["Media: the voice"]
        direction LR
        M1["Carrier / agent phone"] -- "RTP / SRTP (UDP 10000-20000)" --> M2["RTPengine"] --> M3["Asterisk"]
    end
    subgraph CTL["Control and data: the business logic"]
        direction LR
        C1["Agent Desktop"] -- "REST + WebSocket" --> C2["API"] -- "bus" --> C3["Telephony gateway"] -- "AMI / ARI (TCP)" --> C4["Asterisk"]
    end
```

| Plane | Protocols | Ports | Who talks |
|---|---|---|---|
| Signaling | SIP over UDP/TLS, SIP over WSS | 5060, 5061, 443 | carrier, phones, browser to SBC to Asterisk |
| Media | RTP / SRTP | UDP 10000-20000 | carrier, phones, browser to RTPengine to Asterisk |
| Control | AMI (5038) and ARI (8088 HTTP/WS) | internal only | telephony gateway to Asterisk |
| Application | REST/JSON, WebSocket, OIDC | 443 | browser to API, API to auth, CRM adapter to CRM |
| Events | Redis Streams / NATS | internal | gateway publishes, API and adapters consume |
| Data | SQL (ODBC from Asterisk), S3 API | internal | Asterisk, API, recording processor to the databases |

### 7.3 Deployment and network zones

```mermaid
flowchart TB
    INET(("Internet / carrier network"))
    subgraph DMZ["DMZ: public IPs, firewall allows SIP, RTP, 443 only"]
        FW1["Firewall"]
        SBCs["SBC x2 + RTPengine x2"]
        NG["Nginx x2"]
    end
    subgraph TEL["Telephony subnet"]
        A1["Asterisk A (active)"]
        A2["Asterisk B (standby)"]
        VIP["Floating IP / keepalived"]
    end
    subgraph APPN["Application subnet"]
        API1["API x2"]
        TG["Telephony gateway x2"]
        BUSN["Redis / NATS x3"]
        OTH["Recording processor, CRM adapter, IdP"]
    end
    subgraph DATAN["Data subnet: no inbound from outside"]
        PGP[("PostgreSQL primary")]
        PGR[("PostgreSQL replica")]
        OBJ[("Object storage")]
    end
    INET --> FW1 --> SBCs
    FW1 --> NG
    SBCs --> VIP
    VIP --- A1
    VIP --- A2
    NG --> API1
    A1 --> TG
    TG --> BUSN
    BUSN --> API1
    A1 --> PGP
    PGP --> PGR
    OTH --> OBJ
    A1 -. "shared recording volume or sync" .- A2
```

### 7.4 Service count

| Tier | Services | Count (approx.) |
|---|---|---|
| Small production (up to ~50-100 agents, one site) | Asterisk (1 active + 1 warm standby), API, telephony gateway, PostgreSQL (primary + backup), object storage, reverse proxy, monitoring stack | about 8-10 services, 12-15 instances |
| Full HA production | Everything in 7.1: SBC, RTPengine, Nginx, Asterisk x2, gateway x2, bus x3, API x2+, recording processor x2, CRM adapter, IdP x2, PostgreSQL primary + replica, object storage, Vault, monitoring, SIP capture | about 16-18 distinct services, 30+ instances |

Distinct services: SBC, media relay, reverse proxy, Asterisk, telephony gateway, message bus, API, recording processor, CRM adapter, identity provider, Agent/Supervisor UI, PostgreSQL, object storage, secrets store, monitoring, log store, SIP capture, CI/CD.

### 7.5 Decisions that change the design

1. **Asterisk does not cluster queues.** Each node keeps its own queue state, so two active nodes cannot share one queue. Options:
   - Active + standby with a floating IP: simplest, suits a few hundred agents; waiting callers are lost on failover.
   - Multiple active nodes, each with its own agents: needs Kamailio routing and agents pinned to a node.
   - Queue logic outside Asterisk (ARI app or a dedicated ACD): most flexible, most work.
2. **One service owns the AMI/ARI connection.** In the POC the API holds it directly. In production a telephony gateway holds it, publishes events to the bus and sends commands back; otherwise every API instance would open its own connection and duplicate events.
3. **Recordings leave the PBX quickly.** Asterisk writes WAV locally; a recording processor converts, encrypts, uploads to object storage, writes metadata, and applies retention (PDPA).
4. **CDR goes to PostgreSQL**, not SQLite, using Asterisk's ODBC CDR/CEL modules.
5. **Browsers need WebRTC:** WSS and DTLS-SRTP on Asterisk, plus a TURN server for agents behind strict firewalls.
6. **Security:** SIP over TLS and SRTP, SIP scanner blocking at the SBC, AMI/ARI reachable only from the gateway, per-user API tokens via SSO, Vault instead of lab passwords.

### 7.6 POC components mapped to production

```mermaid
flowchart LR
    subgraph POC["POC today"]
        P1["pbx (Asterisk)"]
        P2["api (FastAPI): AMI + REST + WS + demo"]
        P3["sim (Asterisk): fake carrier + agents"]
        P4["SQLite CDR + local recording folder"]
        P5["Mock CRM webhook"]
        P6["demo.html"]
    end
    subgraph PROD["Production"]
        R1["Asterisk x2 (HA)"]
        R2["Telephony gateway + bus + API (split)"]
        R3["Real carrier trunks + SBC + RTPengine"]
        R4["PostgreSQL + object storage + recording processor"]
        R5["CRM adapter to real CRM"]
        R6["Agent Desktop + Supervisor UI"]
    end
    P1 --> R1
    P2 --> R2
    P3 --> R3
    P4 --> R4
    P5 --> R5
    P6 --> R6
```
