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
}

# Modules of the platform (keys used by the timeline and by the Flow Map page)
MODULES = {
    "customer": "ลูกค้า (softphone / โทรศัพท์)",
    "carrier": "Carrier / SIP trunk (sim)",
    "pjsip": "Asterisk PJSIP (SIP + WebRTC)",
    "ivr": "Dialplan: IVR (ivr-main)",
    "queue": "app_queue (queue-flow)",
    "agent": "Agent phone (SIP.js / softphone)",
    "recorder": "MixMonitor (บันทึกเสียง)",
    "cdr": "CDR + queue_log",
    "api": "Integration API (FastAPI)",
    "crm": "CRM / Agent app",
}
