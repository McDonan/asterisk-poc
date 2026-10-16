#!/bin/sh
# Generates IVR prompts (8 kHz, 16-bit, mono wav) into /var/lib/asterisk/sounds/poc
# Robotic espeak-ng fallback; the committed neural prompts (tools_gen_neural.py -> prompts/) take precedence.
# Menu = MTI call center 1484 (Muang Thai Insurance)
set -eu

OUT=/var/lib/asterisk/sounds/poc
mkdir -p "$OUT"

gen() { # name voice text  (skipped when a neural-TTS wav from asterisk/prompts was copied in)
    [ -s "$OUT/$1.wav" ] && return 0
    espeak-ng -v "$2" -s 140 --stdout "$3" \
        | sox -t wav - -r 8000 -c 1 -b 16 "$OUT/$1.wav"
}

# Thai
gen th-welcome   th "ขอบคุณที่ใช้บริการ ศูนย์บริการลูกค้า บริษัท เมืองไทยประกันภัย จำกัด มหาชน"
gen th-menu      th "แจ้งอุบัติเหตุ ตลอด 24 ชั่วโมง กด 1 แจ้งเหตุอื่นๆ กด 2 สอบถามประกันภัยรถยนต์ กด 3 สอบถามประกันภัยอื่นๆ กด 4 สนใจซื้อประกันภัย กด 5 เมืองไทย เฟรนด์สคลับ กด 6 ฟังเมนูอีกครั้ง กด 9"
gen th-invalid   th "ท่านกดหมายเลขไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง"
gen th-holdon    th "กรุณารอสักครู่ เจ้าหน้าที่กำลังรับสายค่ะ"
gen th-noagent   th "ขออภัย ขณะนี้ไม่มีเจ้าหน้าที่ว่าง กรุณาโทรใหม่ภายหลัง"
gen th-goodbye   th "ขอบคุณที่ใช้บริการค่ะ สวัสดี"

# English
gen en-welcome   en "Thank you for calling Muang Thai Insurance customer service 1484."
gen en-menu      en "To report an accident, available 24 hours, press 1. To report other incidents, press 2. For motor insurance inquiries, press 3. For other insurance inquiries, press 4. To buy insurance, press 5. For Muang Thai Friends Club, press 6. To repeat this menu, press 9."
gen en-invalid   en "Invalid option. Please try again."
gen en-holdon    en "Please hold while we connect you to an agent."
gen en-noagent   en "Sorry, no agent is available. Please call back later."
gen en-goodbye   en "Thank you for calling. Goodbye."
