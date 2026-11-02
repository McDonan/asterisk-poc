"""Shared labels: the real MTI 1484 IVR menu, queue names and the modules of the platform."""

# IVR menu of 1484 (Muang Thai Insurance customer service and 24h accident hotline)
IVR_MENU = {
    "1": {"service": "accident", "queue": "q-accident", "th": "แจ้งอุบัติเหตุ (24 ชม.)"},
    "2": {"service": "incident", "queue": "q-incident", "th": "แจ้งเหตุอื่น ๆ"},
    "3": {"service": "motor", "queue": "q-motor", "th": "สอบถามประกันรถยนต์"},
    "4": {"service": "insurance", "queue": "q-insurance", "th": "สอบถามประกันอื่น ๆ"},
    "5": {"service": "sales", "queue": "q-sales", "th": "ซื้อประกัน"},
    "6": {"service": "club", "queue": "q-club", "th": "เมืองไทย เฟรนด์สคลับ"},
}
SERVICE_DIGIT = {v["service"]: d for d, v in IVR_MENU.items()}
QUEUES = [v["queue"] for v in IVR_MENU.values()]
QUEUE_TH = {v["queue"]: v["th"] for v in IVR_MENU.values()}

PROMPT_TH = {
    "th-welcome": "ข้อความต้อนรับ", "th-menu": "เมนูเลือกบริการ (กด 1-6)", "th-invalid": "กดผิด / ไม่มีตัวเลือกนี้",
    "th-goodbye": "ขอบคุณ / วางสาย", "th-holdon": "กรุณารอสักครู่ (เข้าคิว)", "th-noagent": "ขณะนี้ไม่มีเจ้าหน้าที่",
    "th-rate": "ให้คะแนนเจ้าหน้าที่ (กด 1-5)",
    "th-closed": "แจ้งนอกเวลาทำการ", "th-fallback-menu": "เมนู: 1 ฝากข้อความ / 2 โทรกลับเบอร์นี้ / 3 โทรกลับเบอร์อื่น",
    "th-vm-intro": "กรุณาฝากข้อความหลังสัญญาณ", "th-callback-number": "กรอกเบอร์ที่ให้โทรกลับ (จบด้วย #)",
    "th-callback-slot": "เลือกช่วงเวลาติดต่อกลับ (กด 1-3)", "th-callback-ok": "บันทึกการขอให้โทรกลับแล้ว",
}

# Business hours of the call center (the dialplan [sub-hours] must use the same schedule). q-accident is 24 h.
BUSINESS_HOURS = {"days": [0, 1, 2, 3, 4], "open": "08:30", "close": "17:30", "tz_offset_hours": 7,
                  "text": "จันทร์-ศุกร์ 08:30-17:30 (Asia/Bangkok); แจ้งอุบัติเหตุ (1) ตลอด 24 ชม."}

# Why the caller was offered voicemail / callback instead of an agent
FALLBACK_REASON_TH = {"after_hours": "นอกเวลาทำการ", "no_agent": "ไม่มี Agent ว่างรับสาย", "queue_timeout": "รอคิวนานเกินกำหนด"}

# Callback time slots the customer can choose (key = digit pressed)
CALLBACK_SLOTS = {"1": "เช้า (08:30-12:00)", "2": "บ่าย (12:00-15:00)", "3": "เย็น (15:00-17:30)"}

# Modules of the platform (keys used by the timeline and by the Flow Map page)
MODULES = {
    "customer": "ลูกค้า (softphone / โทรศัพท์)",
    "carrier": "Carrier / SIP trunk (sim)",
    "pjsip": "Asterisk PJSIP (SIP + WebRTC)",
    "ivr": "Dialplan: IVR (ivr-main)",
    "queue": "app_queue (queue-flow)",
    "agent": "Agent phone (SIP.js / softphone)",
    "recorder": "MixMonitor (บันทึกเสียง)",
    "voicemail": "Voicemail (app_voicemail)",
    "callback": "Callback list (รายการขอให้โทรกลับ)",
    "cdr": "CDR + queue_log",
    "api": "Integration API (FastAPI)",
    "crm": "CRM / Agent app",
}
