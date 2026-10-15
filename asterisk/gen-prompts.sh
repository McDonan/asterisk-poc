#!/bin/sh
# Generates IVR prompts (8 kHz, 16-bit, mono wav) into /var/lib/asterisk/sounds/poc
set -eu

OUT=/var/lib/asterisk/sounds/poc
mkdir -p "$OUT"

gen() { # name voice text
    espeak-ng -v "$2" -s 140 --stdout "$3" \
        | sox -t wav - -r 8000 -c 1 -b 16 "$OUT/$1.wav"
}

# Thai
gen th-welcome   th "สวัสดีค่ะ ยินดีต้อนรับสู่ระบบคอลเซ็นเตอร์ทดสอบ"
gen th-menu      th "แจ้งเคลม กด 1 สอบถามกรมธรรม์ กด 2 ติดต่อเจ้าหน้าที่ กด 0 ฟังเมนูอีกครั้ง กด 9"
gen th-invalid   th "ท่านกดหมายเลขไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง"
gen th-holdon    th "กรุณารอสักครู่ เจ้าหน้าที่กำลังรับสายค่ะ"
gen th-noagent   th "ขออภัย ขณะนี้ไม่มีเจ้าหน้าที่ว่าง กรุณาโทรใหม่ภายหลัง"
gen th-goodbye   th "ขอบคุณที่ใช้บริการค่ะ สวัสดี"

# English
gen en-welcome   en "Welcome to the call center proof of concept."
gen en-menu      en "For claims, press 1. For policy inquiries, press 2. For an agent, press 0. To repeat this menu, press 9."
gen en-invalid   en "Invalid option. Please try again."
gen en-holdon    en "Please hold while we connect you to an agent."
gen en-noagent   en "Sorry, no agent is available. Please call back later."
gen en-goodbye   en "Thank you for calling. Goodbye."
