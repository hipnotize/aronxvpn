from fastapi import FastAPI, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import text
from dotenv import load_dotenv
import os
import urllib.parse
import secrets
import string

from .database import engine, Base, SessionLocal
from .models import User, InviteCode
from .utils import generate_vpn_uuid
from .xui_client import create_vpn, remove_vpn

load_dotenv()

app = FastAPI(title="AronxVPN API")
Base.metadata.create_all(bind=engine)


def get_db() -> Session:
    return SessionLocal()


def _get_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        raise HTTPException(status_code=500, detail=f"Missing env var: {name}")
    return str(v).strip()


def _pick_reality_sid(raw: str) -> str:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts[0] if parts else raw.strip()


def build_vless_link(uuid: str) -> str:
    server_ip = _get_env("VPN_SERVER_IP")
    port = _get_env("VPN_SERVER_PORT")

    pbk = _get_env("REALITY_PBK")
    sid_raw = _get_env("REALITY_SID")
    sni = _get_env("REALITY_SNI")
    fp = os.getenv("REALITY_FP", "chrome").strip() or "chrome"

    # spiderX (в панели у тебя "/")
    spx = os.getenv("REALITY_SPX", "/").strip() or "/"

    sid = _pick_reality_sid(sid_raw)

    pbk_q = urllib.parse.quote(pbk, safe="")
    sid_q = urllib.parse.quote(sid, safe="")
    sni_q = urllib.parse.quote(sni, safe="")
    fp_q = urllib.parse.quote(fp, safe="")
    spx_q = urllib.parse.quote(spx, safe="")

    return (
        f"vless://{uuid}@{server_ip}:{port}/"
        f"?type=tcp"
        f"&encryption=none"
        f"&security=reality"
        f"&pbk={pbk_q}"
        f"&fp={fp_q}"
        f"&sni={sni_q}"
        f"&sid={sid_q}"
        f"&spx={spx_q}"
        f"&flow=xtls-rprx-vision"
        f"#AronxVPN"
    )


def gen_invite_code(length: int = 10) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health/db")
def db_health():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"database": "connected"}
    except Exception as e:
        return {"database": "error", "detail": str(e)}


# ---- ADMIN ----

@app.post("/admin/create-invite")
def admin_create_invite(x_admin_token: str | None = Header(default=None)):
    admin_token = _get_env("ADMIN_TOKEN")
    if not x_admin_token or x_admin_token != admin_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    db = get_db()
    try:
        for _ in range(5):
            code = gen_invite_code()
            exists = db.query(InviteCode).filter(InviteCode.code == code).first()
            if not exists:
                invite = InviteCode(code=code, is_used=False)
                db.add(invite)
                db.commit()
                db.refresh(invite)
                return {"invite_code": invite.code, "is_used": invite.is_used}

        raise HTTPException(status_code=500, detail="Failed to generate unique invite code")
    finally:
        db.close()


# ---- USER FLOW ----

@app.post("/invite/use")
def use_invite(invite_code: str, telegram_id: str, username: str | None = None):
    db = get_db()
    try:
        # если уже есть пользователь — просто отдадим его ссылку (повторная регистрация не нужна)
        existing = db.query(User).filter(User.telegram_id == telegram_id).first()
        if existing:
            return {"vless_link": build_vless_link(existing.vpn_uuid), "existing": True}

        inv = db.query(InviteCode).filter(InviteCode.code == invite_code).first()
        if not inv:
            raise HTTPException(status_code=404, detail="Invite code not found")
        if inv.is_used:
            raise HTTPException(status_code=409, detail="Invite code already used")

        # создаём VPN клиента в x-ui
        uuid = generate_vpn_uuid()
        try:
            create_vpn(uuid)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"x-ui error: {e}")

        user = User(
            telegram_id=telegram_id,
            username=username,
            vpn_uuid=uuid
        )
        db.add(user)

        inv.is_used = True
        inv.used_by_telegram_id = telegram_id
        inv.used_by_username = username
        inv.used_at = text("CURRENT_TIMESTAMP")

        db.commit()

        return {"vless_link": build_vless_link(uuid), "existing": False}
    finally:
        db.close()


@app.get("/me")
def me(telegram_id: str):
    db = get_db()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found. Ask admin for invite code and use /start in bot.")
        return {"vless_link": build_vless_link(user.vpn_uuid)}
    finally:
        db.close()


# ✅ ДОБАВИЛ: сброс/перевыпуск VPN (новый UUID) для текущего telegram_id
@app.post("/me/reset")
def me_reset(telegram_id: str):
    db = get_db()
    try:
        user = db.query(User).filter(User.telegram_id == telegram_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        old_uuid = user.vpn_uuid
        new_uuid = generate_vpn_uuid()

        # 1) пробуем удалить старого клиента (если remove_vpn глючит — не валим сброс)
        try:
            remove_vpn(old_uuid)
        except Exception:
            pass

        # 2) создаём нового клиента
        try:
            create_vpn(new_uuid)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"x-ui error: {e}")

        # 3) обновляем UUID в БД
        user.vpn_uuid = new_uuid
        db.commit()

        return {
            "vless_link": build_vless_link(new_uuid),
            "old_uuid": old_uuid,
            "new_uuid": new_uuid,
        }
    finally:
        db.close()
