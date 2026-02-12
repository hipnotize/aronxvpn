from fastapi import FastAPI
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os

from .database import engine, Base, SessionLocal
from .models import User
from .utils import generate_vpn_uuid
from .xui_client import create_vpn

load_dotenv()

app = FastAPI()
Base.metadata.create_all(bind=engine)

@app.get("/")
def root():
    return {"status": "ok"}

@app.post("/create")
def create_user(telegram_id: str, username: str | None = None):
    db: Session = SessionLocal()

    existing = db.query(User).filter(User.telegram_id == telegram_id).first()
    if existing:
        return {"error": "User exists"}

    uuid = generate_vpn_uuid()

    create_vpn(uuid)

    user = User(
        telegram_id=telegram_id,
        username=username,
        vpn_uuid=uuid
    )

    db.add(user)
    db.commit()

    server_ip = os.getenv("VPN_SERVER_IP")
    port = os.getenv("VPN_SERVER_PORT")

    link = f"vless://{uuid}@{server_ip}:{port}?type=tcp&security=reality&flow=xtls-rprx-vision#VPN"

    return {"vless_link": link}
