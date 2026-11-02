"""Generate the IVR prompts with neural TTS (Microsoft Edge voices) into asterisk/prompts/*.wav (8 kHz mono).
Run on the host (needs internet + macOS afconvert or ffmpeg):  python3 -m venv /tmp/v && /tmp/v/bin/pip install edge-tts && /tmp/v/bin/python asterisk/tools_gen_neural.py
Pass prompt names to regenerate only those:  ... tools_gen_neural.py th-closed th-noagent
The wavs are committed; the Docker build uses them and only falls back to espeak-ng when a file is missing."""
import asyncio, pathlib, shutil, subprocess, sys, tempfile
import edge_tts

OUT = pathlib.Path(__file__).parent / "prompts"
TH, EN = "th-TH-PremwadeeNeural", "en-US-JennyNeural"
PROMPTS = {
    "th-welcome": (TH, "ขอบคุณที่ใช้บริการ ศูนย์บริการลูกค้า บริษัท เมืองไทยประกันภัย จำกัด มหาชน ค่ะ"),
    "th-menu": (TH, "แจ้งอุบัติเหตุ ตลอด 24 ชั่วโมง กด 1. แจ้งเหตุอื่นๆ กด 2. สอบถามประกันภัยรถยนต์ กด 3. "
                    "สอบถามประกันภัยอื่นๆ กด 4. สนใจซื้อประกันภัย กด 5. เมืองไทย เฟรนด์สคลับ กด 6. ฟังเมนูอีกครั้ง กด 9."),
    "th-invalid": (TH, "ท่านกดหมายเลขไม่ถูกต้อง กรุณาลองใหม่อีกครั้งค่ะ"),
    "th-holdon": (TH, "กรุณารอสักครู่ เจ้าหน้าที่กำลังรับสายค่ะ"),
    "th-noagent": (TH, "ขออภัยค่ะ ขณะนี้เจ้าหน้าที่ทุกท่านไม่ว่างรับสาย"),
    "th-closed": (TH, "ขออภัยค่ะ ขณะนี้อยู่นอกเวลาทำการ ศูนย์บริการลูกค้าเปิดให้บริการวันจันทร์ ถึงวันศุกร์ เวลา แปดโมงครึ่ง ถึง ห้าโมงครึ่งค่ะ"),
    "th-fallback-menu": (TH, "หากต้องการฝากข้อความเสียง กด 1. หากต้องการให้เจ้าหน้าที่โทรกลับมายังเบอร์ที่ท่านใช้โทรเข้ามา กด 2. "
                             "หากต้องการให้โทรกลับไปยังเบอร์อื่น กด 3."),
    "th-vm-intro": (TH, "กรุณาฝากข้อความหลังสัญญาณ โดยแจ้งชื่อ เบอร์ติดต่อกลับ และเรื่องที่ต้องการ เมื่อเสร็จแล้วให้วางสายได้เลยค่ะ"),
    "th-callback-number": (TH, "กรุณากดเบอร์โทรศัพท์ที่ต้องการให้ติดต่อกลับ แล้วกดปุ่มสี่เหลี่ยม เมื่อเสร็จสิ้นค่ะ"),
    "th-callback-slot": (TH, "กรุณาเลือกช่วงเวลาที่สะดวกให้เจ้าหน้าที่ติดต่อกลับ. ช่วงเช้า แปดโมงครึ่ง ถึงเที่ยง กด 1. "
                             "ช่วงบ่าย เที่ยง ถึง บ่ายสามโมง กด 2. ช่วงเย็น บ่ายสามโมง ถึง ห้าโมงครึ่ง กด 3."),
    "th-callback-ok": (TH, "ระบบบันทึกข้อมูลเรียบร้อยแล้วค่ะ เจ้าหน้าที่จะติดต่อกลับตามช่วงเวลาที่ท่านเลือก ขอบคุณที่ใช้บริการค่ะ"),
    "th-goodbye": (TH, "ขอบคุณที่ใช้บริการค่ะ สวัสดีค่ะ"),
    "en-welcome": (EN, "Thank you for calling Muang Thai Insurance customer service 1484."),
    "en-menu": (EN, "To report an accident, available 24 hours, press 1. To report other incidents, press 2. "
                    "For motor insurance inquiries, press 3. For other insurance inquiries, press 4. "
                    "To buy insurance, press 5. For Muang Thai Friends Club, press 6. To repeat this menu, press 9."),
    "en-invalid": (EN, "Invalid option. Please try again."),
    "en-holdon": (EN, "Please hold while we connect you to an agent."),
    "en-noagent": (EN, "Sorry, no agent is available. Please call back later."),
    "en-goodbye": (EN, "Thank you for calling. Goodbye."),
}


def to_wav(src: pathlib.Path, dst: pathlib.Path) -> None:
    if shutil.which("afconvert"):
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@8000", "-c", "1", str(src), str(dst)], check=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-ar", "8000", "-ac", "1", str(dst)], check=True)


async def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, (voice, text) in PROMPTS.items():
            if sys.argv[1:] and name not in sys.argv[1:]:
                continue
            mp3 = pathlib.Path(tmp) / f"{name}.mp3"
            await edge_tts.Communicate(text, voice, rate="-8%").save(str(mp3))
            to_wav(mp3, OUT / f"{name}.wav")
            print("ok", name)

asyncio.run(main())
