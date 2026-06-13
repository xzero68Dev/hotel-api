from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import anthropic
import os
import random
import string
from supabase import create_client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Clients
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

# Suspicious patterns
SUSPICIOUS = [
    "ignore previous", "system prompt", "jailbreak",
    "pretend you are", "forget instructions", "you are now",
    "disregard", "override"
]

SYSTEM = """คุณคือ AI assistant ของ Sea Breeze Resort Phuket โรงแรม boutique ที่ Kamala Beach

สไตล์การตอบ:
- กระชับ เป็นกันเอง ไม่เกิน 3 ประโยค
- ตอบทุกภาษาที่แขกใช้ (ไทย อังกฤษ จีน รัสเซีย)
- ถ้าถามนอกเรื่องโรงแรมเกิน 2 ครั้ง ให้บอกว่าตอบได้เฉพาะเรื่องการจองค่ะ

เมื่อแขกต้องการจองและบอกครบ (ห้อง + เช็คอิน + เช็คเอาท์ + ชื่อแขก) ให้ตอบด้วย format:
[BOOKING:ชื่อห้อง|เช็คอิน|เช็คเอาท์|จำนวนคืน|ราคารวม|ชื่อแขก|notes]

ข้อมูลโรงแรม:
- Check-in 14:00 / Check-out 12:00
- จองตรงได้ Breakfast ฟรี 2 ท่าน และถูกกว่า OTA 15%
- มีสระว่ายน้ำ ชายหาด สปา ร้านอาหาร"""

# Models
class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    session_msg_count: Optional[int] = 0

class BookingRequest(BaseModel):
    room_name: str
    check_in: str
    check_out: str
    nights: int
    total: int
    guest_name: str
    notes: Optional[str] = ""

# Helpers
def make_booking_id():
    return "SBR" + "".join(random.choices(string.digits, k=4))

def get_rooms_text():
    res = supabase.table("rooms").select("*").execute()
    rooms = res.data
    lines = []
    for r in rooms:
        lines.append(f"- {r['name']}: ฿{r['price']:,}/คืน ว่าง {r['available']} ห้อง ({r['description']})")
    return "\n".join(lines)

# Routes
@app.get("/")
def root():
    return {"status": "Sea Breeze Resort API running"}

@app.get("/rooms")
def get_rooms():
    res = supabase.table("rooms").select("*").execute()
    return {"rooms": res.data}

@app.post("/chat")
def chat(req: ChatRequest):
    last_msg = req.messages[-1].content

    # ชั้น 1: เช็คความยาวผิดปกติ
    if len(last_msg) > 500:
        return {"reply": "ขอโทษค่ะ ข้อความยาวเกินไปค่ะ กรุณาพิมพ์สั้นลงนะคะ"}

    # ชั้น 2: เช็ค suspicious pattern
    if any(p in last_msg.lower() for p in SUSPICIOUS):
        return {"reply": "ขอโทษค่ะ ตอบคำถามนี้ไม่ได้ค่ะ มีอะไรให้ช่วยเรื่องการจองห้องมั้ยคะ?"}

    # ชั้น 3: ดึงห้องว่าง real-time จาก Supabase
    rooms_text = get_rooms_text()
    system_with_rooms = SYSTEM + f"\n\nห้องที่ว่างตอนนี้:\n{rooms_text}"

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system_with_rooms,
        messages=[{"role": m.role, "content": m.content} for m in req.messages]
    )
    return {"reply": response.content[0].text}

@app.post("/bookings")
def create_booking(req: BookingRequest):
    # เช็คห้องว่างก่อน
    room = supabase.table("rooms").select("*").eq("name", req.room_name).execute()
    if not room.data or room.data[0]["available"] < 1:
        return {"success": False, "message": "ขอโทษค่ะ ห้องเต็มแล้วค่ะ"}

    booking_id = make_booking_id()

    # บันทึก booking
    supabase.table("bookings").insert({
        "id": booking_id,
        "room_id": room.data[0]["id"],
        "room_name": req.room_name,
        "check_in": req.check_in,
        "check_out": req.check_out,
        "nights": req.nights,
        "total": req.total,
        "guest_name": req.guest_name,
        "notes": req.notes
    }).execute()

    # ลดจำนวนห้องว่าง
    supabase.table("rooms").update({
        "available": room.data[0]["available"] - 1
    }).eq("id", room.data[0]["id"]).execute()

    return {"success": True, "booking_id": booking_id}

@app.get("/bookings")
def get_bookings():
    res = supabase.table("bookings").select("*").order("created_at", desc=True).execute()
    return {"bookings": res.data}
