from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import anthropic
import os
import random
import string
from supabase import create_client
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
supabase = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

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

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    session_id: Optional[str] = None
    ip: Optional[str] = None

class BookingRequest(BaseModel):
    room_name: str
    check_in: str
    check_out: str
    nights: int
    total: int
    guest_name: str
    notes: Optional[str] = ""
    session_id: Optional[str] = None

def make_booking_id():
    return "SBR" + "".join(random.choices(string.digits, k=4))

def get_rooms_text():
    res = supabase.table("rooms").select("*").execute()
    lines = []
    for r in res.data:
        lines.append(f"- {r['name']}: ฿{r['price']:,}/คืน ว่าง {r['available']} ห้อง ({r['description']})")
    return "\n".join(lines)

def log_session(session_id, messages, ip=None):
    try:
        existing = supabase.table("sessions").select("id").eq("id", session_id).execute()
        msgs_json = [{"role": m.role, "content": m.content} for m in messages]
        if existing.data:
            supabase.table("sessions").update({
                "messages": msgs_json,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("id", session_id).execute()
        else:
            supabase.table("sessions").insert({
                "id": session_id,
                "ip": ip or "unknown",
                "messages": msgs_json
            }).execute()
    except:
        pass  # log fail ไม่ควร block chat

@app.get("/")
def root():
    return {"status": "Sea Breeze Resort API running"}

@app.get("/rooms")
def get_rooms():
    res = supabase.table("rooms").select("*").execute()
    return {"rooms": res.data}

@app.get("/bookings")
def get_bookings():
    res = supabase.table("bookings").select("*").order("created_at", desc=True).execute()
    return {"bookings": res.data}

@app.get("/sessions")
def get_sessions():
    res = supabase.table("sessions").select("id,ip,created_at,updated_at").order("updated_at", desc=True).execute()
    return {"sessions": res.data}

@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    last_msg = req.messages[-1].content

    if len(last_msg) > 500:
        return {"reply": "ขอโทษค่ะ ข้อความยาวเกินไปค่ะ"}

    if any(p in last_msg.lower() for p in SUSPICIOUS):
        return {"reply": "ขอโทษค่ะ ตอบคำถามนี้ไม่ได้ค่ะ มีอะไรให้ช่วยเรื่องการจองมั้ยคะ?"}

    rooms_text = get_rooms_text()
    system_with_rooms = SYSTEM + f"\n\nห้องที่ว่างตอนนี้:\n{rooms_text}"

    response = claude.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=600,
        system=system_with_rooms,
        messages=[{"role": m.role, "content": m.content} for m in req.messages]
    )
    reply = response.content[0].text

    # log session
    if req.session_id:
        ip = request.headers.get("x-forwarded-for", request.client.host)
        all_msgs = list(req.messages) + [Message(role="assistant", content=reply)]
        log_session(req.session_id, all_msgs, ip)

    return {"reply": reply}

@app.post("/bookings")
def create_booking(req: BookingRequest):
    room = supabase.table("rooms").select("*").eq("name", req.room_name).execute()
    if not room.data or room.data[0]["available"] < 1:
        return {"success": False, "message": "ขอโทษค่ะ ห้องเต็มแล้วค่ะ"}

    booking_id = make_booking_id()

    supabase.table("bookings").insert({
        "id": booking_id,
        "room_id": room.data[0]["id"],
        "room_name": req.room_name,
        "check_in": req.check_in,
        "check_out": req.check_out,
        "nights": req.nights,
        "total": req.total,
        "guest_name": req.guest_name,
        "notes": req.notes,
        "session_id": req.session_id
    }).execute()

    supabase.table("rooms").update({
        "available": room.data[0]["available"] - 1
    }).eq("id", room.data[0]["id"]).execute()

    return {"success": True, "booking_id": booking_id}
