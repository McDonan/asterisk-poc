# สรุปผล POC: Asterisk เป็น Core Telephony ของ Call Center (MTI)

เวอร์ชันที่ทดสอบ: Asterisk 22.11.0 (LTS, build จาก source, PJSIP) — รันด้วย Docker compose
วิธีทดสอบ: E2E อัตโนมัติ [tests/e2e.py](../tests/e2e.py) (scenario S0–S7) ผลล่าสุด **42/42 checks ผ่าน**
วิธีรัน/ตัวอย่าง API ดู [README.md](../README.md)

## 1. สรุปสำหรับผู้บริหาร

**Asterisk ใช้เป็น core telephony ได้** สำหรับความสามารถหลักที่ขอทั้งหมด (SIP/Extension, Inbound, IVR/DTMF, Queue,
Agent control, Recording, CDR, API, Monitoring และ Outbound) โดย Asterisk ทำหน้าที่ "เครื่องยนต์โทรศัพท์"
ส่วนที่ **ต้องพัฒนาเพิ่ม** คือชั้น application รอบ ๆ (Agent app/Softphone, Supervisor dashboard, Reporting, Campaign/Auto-dialer,
User/Role management, HA/Scale, Security hardening) — Asterisk ไม่ได้มาพร้อมชุดนี้

## 2. ผลรายฟีเจอร์

| # | ความสามารถ | ผล | วิธีทำใน POC | หลักฐาน (scenario) |
|---|---|---|---|---|
| 1 | SIP / Extension | ผ่าน | PJSIP endpoint/auth/aor ต่อ agent, registration, trunk | S0 |
| 2 | Inbound call | ผ่าน | trunk → `from-trunk` → IVR | S1 |
| 3 | IVR / DTMF | ผ่าน | `Background` + `WaitExten`, ตัวเลือก 1/2/0, กดผิดแล้ววนซ้ำ, หมด retry วางสาย | S1, S6 |
| 4 | Queue routing | ผ่าน | `app_queue` 3 queue, strategy `leastrecent`, wrap-up, MOH | S1, S2 |
| 5 | Agent handling | ผ่าน | login/logout/pause (feature code + API), answer, hold/unhold, blind transfer ไป agent/queue, hangup ผ่าน API | S1, S3 |
| 6 | Call recording | ผ่าน | `MixMonitor` option `b`, 1 ไฟล์/สาย ครอบคลุมสายที่ transfer, ดาวน์โหลดผ่าน API ได้เป็น WAV ถูกต้อง | S1, S3, S7 |
| 7 | CDR / Call history | ผ่าน | `cdr_sqlite3_custom` + custom field (direction, ivr_choice, recording, dialed, agent, dial_status, hangup_cause, ...) และ `queue_log` | S1, S4, S7 |
| 8 | API / Integration | ผ่าน | FastAPI บน AMI: REST สั่งงาน/ดึงข้อมูล, WebSocket event real-time, webhook screen-pop ไป CRM (mock) | S1, S3, S5 |
| 9 | Basic monitoring | ผ่าน | `/monitor/summary`, `/agents`, `/queues`, `/calls/active`, event stream | S1 |
| 10 | **Outbound call** | ผ่าน | agent กด `9`+เบอร์ หรือ API click-to-call → trunk | S7 |

### Outbound: ผลสาย และสถานะ recording

ทดสอบกับ Carrier จำลอง (ไม่ใช่ trunk จริง) พบว่า **แยกผลสายได้ครบ และรู้ได้ว่ามีไฟล์บันทึกเสียงหรือไม่**

| กรณี | `result` | สาเหตุที่ระบบรู้ | `recording_status` |
|---|---|---|---|
| ปลายทางรับสาย | CONNECTED | `dial_status=ANSWER`, ring/talk seconds | recorded |
| ปลายทางวางเอง | CONNECTED | PBX ปล่อยสาย agent เอง | recorded |
| สายไม่ว่าง | BUSY | `BUSY`, cause 17 | not_recorded |
| ไม่มีคนรับ (20 s) | NO_ANSWER | `NOANSWER`, ring ≈ 20 s | not_recorded |
| Agent วางระหว่างรอดัง | CANCELLED | `CANCEL` | not_recorded |
| Congestion | FAILED | `CONGESTION`, cause 34 | not_recorded |
| เบอร์ไม่มีในระบบ | FAILED | `CHANUNAVAIL`, cause 1 | not_recorded |

- ผลสายแจ้ง real-time ผ่าน event `OutboundDialResult` และเก็บถาวรใน CDR (query ผ่าน `/calls/outbound?result=...`)
- `not_recorded` ของสายไม่ติดเป็นพฤติกรรมที่ตั้งใจ (MixMonitor `b` บันทึกเฉพาะตอนสาย bridge) — ถ้าต้องการเก็บเสียง early media ต้องปรับ/พัฒนาเพิ่ม

## 3. สิ่งที่ต้องรู้ / ข้อจำกัดที่พบระหว่างทำ POC

- **CDR แยกเป็นหลาย leg ต่อ 1 สาย** (agent leg, trunk leg, leg หลัง transfer) — POC ใช้ API รวมด้วย `linkedid` เป็น 1 แถวต่อสาย (`/calls/history`) ระบบจริงควรมี reporting layer ของตัวเอง
- **เวลา talk/ring สำหรับสาย inbound** จาก CDR `billsec` นับตั้งแต่ตอบ IVR (รวมเวลา IVR+รอคิว) ไม่ใช่เวลาคุยกับ agent — ค่าที่ถูกต้องอยู่ใน `queue_log` (`CONNECT` holdtime, `COMPLETE*` talktime) ซึ่ง API มี endpoint `/queue-log` ให้แล้ว ส่วนสาย outbound วัดเองที่ dialplan (`ring_seconds`, `talk_seconds`) ได้ตรง
- **`DIALEDTIME/ANSWEREDTIME` ของ Dial() ถูกตั้งเฉพาะสายที่ถูกรับ** — เวลาดังของสายไม่ติดต้องวัดเองด้วย `${EPOCH}` (ทำไว้แล้วใน [extensions.conf](../asterisk/etc/extensions.conf))
- **การ Hold จาก agent** ทำที่ฝั่ง softphone (SIP re-INVITE) Asterisk ส่ง event `Hold/Unhold` (ยืนยันใน E2E) — การได้ยิน MOH ของลูกค้าจริงยังไม่ได้วัดด้วยหูบน softphone — การสั่ง hold ผ่าน API จากฝั่ง server ไม่ได้ทำไว้
- ทดสอบ hold กับตัวจำลองต้องเปิด `moh_passthrough` ที่ฝั่ง sim (เป็นเรื่องของ test harness ไม่ใช่ PBX)
- การรัน E2E ครั้งแรกทันทีหลัง restart PBX อาจมี scenario พลาดเพราะ registration/queue ยังไม่นิ่ง (รันซ้ำผ่าน)
- config ของ Asterisk แยกเป็นไฟล์ text (`pjsip.conf`, `queues.conf`, `extensions.conf`) การเพิ่ม agent/queue จำนวนมากหรือแก้ผ่าน UI ต้องพัฒนา provisioning (เช่น PJSIP/queue realtime บน DB) เพิ่ม

## 4. Gap — สิ่งที่ยังไม่ได้ทดสอบ และสิ่งที่ต้องพัฒนาเพิ่ม

### ยังไม่ได้ทดสอบใน POC นี้

| เรื่อง | หมายเหตุ |
|---|---|
| SIP trunk กับผู้ให้บริการจริง | Outbound/Inbound ทดสอบกับ Carrier จำลอง; ยังไม่ทราบพฤติกรรมจริง (codec, early media, cause code จริง, CLI/DID, ข้อจำกัด CPS/channel) |
| คุณภาพเสียงบน softphone จริง | E2E ใช้ agent จำลอง; มีชุดทดสอบ softphone ใน README แต่ยังไม่ได้วัด MOS/jitter/NAT |
| Load / Capacity / HA | ยังไม่ได้ทดสอบจำนวนสายพร้อมกัน, failover, active-standby |
| Security hardening | รหัสผ่านเป็นค่า lab, ยังไม่เปิด TLS/SRTP, ยังไม่มี fail2ban/ACL/rate-limit |
| เสียง IVR ภาษาไทย | ไฟล์เสียงใน POC เป็น placeholder (สร้างจาก TTS) ต้องอัดเสียงจริง |
| Attended transfer / conference ผ่าน softphone | ทดสอบ blind transfer; attended transfer มี feature code แต่ยังไม่อยู่ใน E2E |

### ต้องพัฒนาเพิ่ม (Asterisk ไม่มีมาให้)

| เรื่อง | รายละเอียด |
|---|---|
| Agent app / Softphone (WebRTC) | UI สำหรับ agent (รับสาย, hold, transfer, สถานะ, screen-pop) — Asterisk รองรับ WebRTC แต่ต้องทำ client |
| Supervisor dashboard + Listen/Whisper/Barge | มี `ChanSpy` ให้ใช้ แต่ต้องทำ UI และสิทธิ์ |
| Reporting / SLA / Dashboard ย้อนหลัง | ต้องมี data warehouse/ETL จาก CDR + queue_log |
| Campaign / Auto-dialer (predictive/progressive) | นอก scope POC; ต้องพัฒนา call list, pacing, DNC, abandon rate control (หรือใช้ผลิตภัณฑ์ dialer ที่ต่อกับ Asterisk) |
| Number plan / Outbound permission | กำหนดสิทธิ์โทรออกตามประเภทเบอร์ (มือถือ/ต่างประเทศ/ premium), prefix, ตัวเลือก trunk, least-cost routing |
| Recording management | retention, เข้ารหัส, สิทธิ์เข้าถึง, ส่งขึ้น object storage, redact/pause recording ช่วงรับข้อมูลอ่อนไหว, early media |
| User / Role / Provisioning | จัดการ agent, queue, skill-based routing, schedule/วันหยุด (ปัจจุบันแก้ไฟล์ config) |
| Skill-based / priority routing | `app_queue` รองรับ penalty/ring strategy พื้นฐาน; skill ซับซ้อนต้องออกแบบ |
| Callback / Voicemail / SMS / Omni-channel | ไม่ได้ครอบคลุมใน POC |
| Production architecture | HA/Failover (Kamailio + หลาย Asterisk หรือ DB realtime), monitoring (Prometheus/Grafana), logging, backup, CI/CD, การอัปเกรด |
| Compliance | PDPA/การแจ้งบันทึกเสียง, audit log, การเก็บ CDR ตามกฎหมาย |

## 5. ข้อเสนอแนะ

1. เห็นควรเดินหน้าใช้ Asterisk เป็น core telephony โดยแยก **Telephony layer (Asterisk)** ออกจาก **Application layer (Agent app, Supervisor, Reporting, Campaign)** ผ่าน AMI/ARI ตามแนวทางใน POC นี้
2. Phase ถัดไป: ทดสอบกับ SIP trunk จริง, ทดสอบ load/คุณภาพเสียง, ออกแบบ HA และ security
3. ตัดสินใจ build vs. buy สำหรับ Auto-dialer และ Supervisor tooling (มีผลิตภัณฑ์/โอเพนซอร์สที่ต่อกับ Asterisk ได้)
