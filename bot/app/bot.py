import os
import io
import asyncio
from typing import Optional, Tuple, Dict

import httpx
import qrcode

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    BufferedInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext


# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
BOT_ADMIN_IDS = {x.strip() for x in os.getenv("BOT_ADMIN_IDS", "").split(",") if x.strip()}

API_BASE = os.getenv("API_BASE_URL", "http://backend:8000").strip().rstrip("/")
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip()  # оставляем как есть (не используем)

API_USE_INVITE = f"{API_BASE}/invite/use"
API_ME = f"{API_BASE}/me"
API_ADMIN_INVITE = f"{API_BASE}/admin/create-invite"
API_HEALTH_DB = f"{API_BASE}/health/db"  # ✅ статус сервиса

# ✅ ДОБАВИЛ: endpoint на сброс/пересоздание VPN (тебе надо добавить его в backend)
API_RESET = f"{API_BASE}/me/reset"

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

# =========================
# Bot init
# =========================
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

HTTP_TIMEOUT = 15.0


# =========================
# UI
# =========================
def is_admin_user(telegram_id: int) -> bool:
    return str(telegram_id) in BOT_ADMIN_IDS


def kb_main(telegram_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔐 Подключиться по инвайту", callback_data="m:register")],
        [
            InlineKeyboardButton(text="📌 Мой VPN", callback_data="m:me"),
            InlineKeyboardButton(text="📖 Инструкция (iOS/Android)", callback_data="m:guide"),
        ],
        [
            InlineKeyboardButton(text="🩺 Статус сервиса", callback_data="m:status"),
            InlineKeyboardButton(text="🆘 Поддержка", callback_data="m:support"),
        ],
    ]

    if is_admin_user(telegram_id) and ADMIN_TOKEN:
        rows.append([InlineKeyboardButton(text="🛠 Админ: создать инвайт", callback_data="a:invite")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:menu")]
    ])


def kb_after_vpn(telegram_id: int, link: str) -> InlineKeyboardMarkup:
    # ✅ это клавиатура, которая точно будет после выдачи ссылки/QR
    rows = [
        [InlineKeyboardButton(text="📷 Получить QR-код", callback_data="m:qr")],
        [
            InlineKeyboardButton(text="📖 Инструкция", callback_data="m:guide"),
            InlineKeyboardButton(text="🆘 Поддержка", callback_data="m:support"),
        ],
        # ✅ ДОБАВИЛ: кнопка сбросить (пересоздать) VPN
        [InlineKeyboardButton(text="🔄 Сбросить VPN (новый код)", callback_data="m:reset")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="m:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_confirm_reset(telegram_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, сбросить", callback_data="m:reset:yes")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="m:menu")],
    ])


def make_qr_png_bytes(text: str) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


WELCOME_TEXT = (
    "👋 Привет! Это *AronxVPN*.\n\n"
    "🔐 Для подключения нужен *инвайт-код*.\n"
    "Нажми кнопку *«Подключиться по инвайту»* и введи код.\n\n"
    "Если ты уже подключался — жми *«Мой VPN»* (бот пришлёт ссылку ещё раз)."
)

GUIDE_TEXT = (
    "📖 *Инструкция подключения*\n\n"
    "✅ *Шаг 1.* Получи VLESS-ссылку (кнопка *Мой VPN*).\n"
    "✅ *Шаг 2.* Установи клиент.\n\n"
    "🍏 *iPhone (iOS)*\n"
    "— Клиенты: *Hiddify*, *Streisand*, *V2Box*, *Shadowrocket*.\n"
    "— Import → Scan QR или Import from clipboard.\n\n"
    "🤖 *Android*\n"
    "— Клиенты: *v2rayNG*, *Hiddify*, *Nekobox*.\n"
    "— + → Import from clipboard или Scan QR.\n\n"
    "Если не коннектится:\n"
    "1) обнови приложение\n"
    "2) импортируй ссылку заново\n"
    "3) проверь дату/время (авто)\n"
)

SUPPORT_TEXT = (
    "🆘 *Поддержка*\n\n"
    "Напиши:\n"
    "— iOS или Android\n"
    "— каким приложением подключаешься\n"
    "— какая ошибка\n"
    "И приложи скрин (если есть)."
)

INVITE_PROMPT = (
    "🔐 Введи *инвайт-код* одним сообщением.\n\n"
    "Пример: `A1B2C3D4E5`\n\n"
    "⬅️ Чтобы отменить — нажми *В меню*."
)


# =========================
# FSM
# =========================
class Flow(StatesGroup):
    waiting_invite = State()


# =========================
# HTTP helpers
# =========================
def _fallback_urls(original_url: str) -> list[str]:
    """
    Если API_BASE_URL указывает на localhost/127.0.0.1/nginx,
    внутри docker это часто не работает. Дадим шанс на http://backend:8000.
    """
    urls = [original_url]

    if "://localhost" in original_url or "://127.0.0.1" in original_url:
        urls.append(original_url.replace("://localhost", "://backend").replace("://127.0.0.1", "://backend"))

    if original_url.startswith("http://backend") and ":8000" not in original_url:
        urls.append(original_url.replace("http://backend", "http://backend:8000"))

    if "http://backend:8000" not in urls:
        path = "/" + original_url.split("://", 1)[-1].split("/", 1)[-1]
        urls.append("http://backend:8000" + path)

    out = []
    for u in urls:
        if u not in out:
            out.append(u)
    return out


async def api_json(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> Tuple[int, Dict]:
    last_err = None

    for try_url in _fallback_urls(url):
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            try:
                r = await client.request(method, try_url, params=params, headers=headers)
            except httpx.RequestError as e:
                last_err = f"{e.__class__.__name__} while requesting {try_url}"
                continue

        try:
            data = r.json()
        except Exception:
            text = (r.text or "")[:300]
            data = {"detail": f"Non-JSON response ({r.status_code}) from {try_url}: {text}"}

        data["_debug_url"] = try_url
        return r.status_code, data

    return 0, {"detail": last_err or "Network error", "_debug_url": url}


# =========================
# Core actions
# =========================
async def send_vpn_link_only(message: Message, link: str, title: str):
    await message.answer(
        (
            f"{title}\n\n"
            "📎 *Ссылка (скопируй и импортируй в клиент):*\n"
            f"`{link}`\n\n"
            "📷 QR-код — по кнопке ниже."
        ),
        parse_mode="Markdown",
        reply_markup=kb_after_vpn(message.from_user.id, link),
    )


async def send_qr_photo(message: Message, link: str, title: str = "📷 QR-код"):
    qr_bytes = make_qr_png_bytes(link)
    photo = BufferedInputFile(qr_bytes, filename="vpn.png")
    await message.answer_photo(
        photo=photo,
        caption=(
            f"{title}\n\n"
            "✅ Отсканируй QR в клиенте или используй ссылку из предыдущего сообщения."
        ),
        reply_markup=kb_after_vpn(message.from_user.id, link),
    )


async def send_my_vpn(message: Message, telegram_id: int):
    status, data = await api_json("GET", API_ME, params={"telegram_id": str(telegram_id)})

    if status == 0:
        dbg = data.get("_debug_url", "")
        await message.answer(
            "⚠️ Сервис временно недоступен. Попробуй позже.\n\n"
            f"🔎 Debug: `{dbg}`\n"
            f"ℹ️ {data.get('detail')}",
            parse_mode="Markdown",
            reply_markup=kb_back(telegram_id),
        )
        return

    if status == 404:
        await message.answer(
            "🔒 У тебя ещё нет доступа.\n\n"
            "Нажми *«Подключиться по инвайту»* и введи код.",
            parse_mode="Markdown",
            reply_markup=kb_main(telegram_id),
        )
        return

    if status >= 400:
        dbg = data.get("_debug_url", "")
        await message.answer(
            f"❌ Ошибка: {data.get('detail', data)}\n\n"
            f"🔎 Debug: `{dbg}`",
            parse_mode="Markdown",
            reply_markup=kb_back(telegram_id),
        )
        return

    link = data.get("vless_link")
    if not link:
        await message.answer("⚠️ Не смог получить ссылку. Напиши в поддержку.", reply_markup=kb_back(telegram_id))
        return

    await send_vpn_link_only(message, link, "📌 *Твой VPN:*")


async def use_invite_and_send(message: Message, code: str):
    tid = str(message.from_user.id)
    username = message.from_user.username

    status, data = await api_json(
        "POST",
        API_USE_INVITE,
        params={"invite_code": code, "telegram_id": tid, "username": username},
    )

    if status == 0:
        dbg = data.get("_debug_url", "")
        await message.answer(
            "⚠️ Не могу достучаться до сервиса. Попробуй позже.\n\n"
            f"🔎 Debug: `{dbg}`\n"
            f"ℹ️ {data.get('detail')}",
            parse_mode="Markdown",
            reply_markup=kb_back(message.from_user.id),
        )
        return

    if status == 404:
        await message.answer("❌ Инвайт-код не найден. Проверь и введи снова.", reply_markup=kb_back(message.from_user.id))
        return

    if status == 409:
        await message.answer("❌ Этот инвайт уже использован. Попроси новый код.", reply_markup=kb_back(message.from_user.id))
        return

    if status >= 400:
        dbg = data.get("_debug_url", "")
        await message.answer(
            f"❌ Ошибка: {data.get('detail', data)}\n\n🔎 Debug: `{dbg}`",
            parse_mode="Markdown",
            reply_markup=kb_back(message.from_user.id),
        )
        return

    link = data.get("vless_link")
    if not link:
        await message.answer("⚠️ Странный ответ сервера. Напиши админу.", reply_markup=kb_back(message.from_user.id))
        return

    existing = data.get("existing", False)
    title = "✅ *Готово!* Ты уже был зарегистрирован — вот твой VPN снова:" if existing else "✅ *Готово!* Подключение создано:"
    await send_vpn_link_only(message, link, title)


async def admin_create_invite(message: Message, requester_id: Optional[int] = None):
    # requester_id нужен, потому что при нажатии кнопки message.from_user == BOT, а не пользователь
    if requester_id is None:
        requester_id = message.from_user.id if message.from_user else 0

    if not is_admin_user(requester_id):
        await message.answer("⛔ Нет доступа.", reply_markup=kb_back(requester_id))
        return
    if not ADMIN_TOKEN:
        await message.answer("⚠️ ADMIN_TOKEN не задан в .env — админ-функции выключены.", reply_markup=kb_back(requester_id))
        return

    status, data = await api_json("POST", API_ADMIN_INVITE, headers={"X-Admin-Token": ADMIN_TOKEN})

    if status == 0:
        dbg = data.get("_debug_url", "")
        await message.answer(
            "⚠️ Backend недоступен.\n\n"
            f"🔎 Debug: `{dbg}`\n"
            f"ℹ️ {data.get('detail')}",
            parse_mode="Markdown",
            reply_markup=kb_back(requester_id),
        )
        return

    if status >= 400:
        dbg = data.get("_debug_url", "")
        await message.answer(
            f"❌ Admin error: {data.get('detail', data)}\n\n🔎 Debug: `{dbg}`",
            parse_mode="Markdown",
            reply_markup=kb_back(requester_id),
        )
        return

    code = data.get("invite_code")
    if not code:
        await message.answer(f"⚠️ Неожиданный ответ: {data}", reply_markup=kb_back(requester_id))
        return

    await message.answer(
        "🛠 *Инвайт создан*\n\n"
        f"Код: `{code}`\n\n"
        "Отправь пользователю код.\n"
        "Он откроет бота → *Подключиться по инвайту* → введёт код.\n\n"
        "Можно ещё так: `/start <CODE>`",
        parse_mode="Markdown",
        reply_markup=kb_back(requester_id),
    )


# ✅ ДОБАВИЛ: сброс VPN (пересоздать код)
async def reset_my_vpn(message: Message, telegram_id: int):
    status, data = await api_json("POST", API_RESET, params={"telegram_id": str(telegram_id)})

    if status == 0:
        dbg = data.get("_debug_url", "")
        await message.answer(
            "⚠️ Не могу сбросить сейчас (backend недоступен).\n\n"
            f"🔎 Debug: `{dbg}`\n"
            f"ℹ️ {data.get('detail')}",
            parse_mode="Markdown",
            reply_markup=kb_back(telegram_id),
        )
        return

    if status >= 400:
        dbg = data.get("_debug_url", "")
        await message.answer(
            f"❌ Ошибка сброса: {data.get('detail', data)}\n\n🔎 Debug: `{dbg}`",
            parse_mode="Markdown",
            reply_markup=kb_back(telegram_id),
        )
        return

    link = data.get("vless_link")
    if not link:
        await message.answer("⚠️ Backend вернул странный ответ. Напиши в поддержку.", reply_markup=kb_back(telegram_id))
        return

    await send_vpn_link_only(message, link, "🔄 *VPN сброшен.* Вот новый доступ:")


# =========================
# Commands
# =========================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()

    # deep-link: /start INVITECODE
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2:
        code = parts[1].strip().upper().replace(" ", "")
        if code:
            await message.answer("🔐 Принял код. Проверяю…", reply_markup=kb_back(message.from_user.id))
            await use_invite_and_send(message, code)
            return

    await message.answer(WELCOME_TEXT, parse_mode="Markdown", reply_markup=kb_main(message.from_user.id))


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(GUIDE_TEXT, parse_mode="Markdown", reply_markup=kb_main(message.from_user.id))


@router.message(Command("me"))
async def cmd_me(message: Message):
    await send_my_vpn(message, message.from_user.id)


@router.message(Command("invite"))
async def cmd_invite(message: Message):
    await admin_create_invite(message, requester_id=message.from_user.id)


# =========================
# Callbacks
# =========================
@router.callback_query(F.data == "m:menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(WELCOME_TEXT, parse_mode="Markdown", reply_markup=kb_main(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "m:guide")
async def cb_guide(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(GUIDE_TEXT, parse_mode="Markdown", reply_markup=kb_main(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "m:support")
async def cb_support(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(SUPPORT_TEXT, parse_mode="Markdown", reply_markup=kb_main(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "m:status")
async def cb_status(call: CallbackQuery, state: FSMContext):
    await state.clear()
    status, data = await api_json("GET", API_HEALTH_DB)

    if status == 0:
        dbg = data.get("_debug_url", "")
        await call.message.edit_text(
            "⚠️ Backend недоступен (сеть/таймаут).\n\n"
            f"🔎 Debug: `{dbg}`\n"
            f"ℹ️ {data.get('detail')}",
            parse_mode="Markdown",
            reply_markup=kb_main(call.from_user.id),
        )
        await call.answer()
        return

    if status >= 400:
        dbg = data.get("_debug_url", "")
        await call.message.edit_text(
            f"❌ Ошибка backend: {data.get('detail', data)}\n\n🔎 Debug: `{dbg}`",
            parse_mode="Markdown",
            reply_markup=kb_main(call.from_user.id),
        )
        await call.answer()
        return

    db_state = data.get("database", "unknown")
    detail = data.get("detail")
    text = f"🩺 *Статус сервиса:*\n\n• Backend: ✅\n• DB: *{db_state}*"
    if detail:
        text += f"\n• Detail: `{detail}`"
    await call.message.edit_text(text, parse_mode="Markdown", reply_markup=kb_main(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "m:register")
async def cb_register(call: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.waiting_invite)
    await call.message.edit_text(INVITE_PROMPT, parse_mode="Markdown", reply_markup=kb_back(call.from_user.id))
    await call.answer()


@router.callback_query(F.data == "m:me")
async def cb_me(call: CallbackQuery):
    await call.answer()
    await send_my_vpn(call.message, call.from_user.id)


@router.callback_query(F.data == "a:invite")
async def cb_admin_invite(call: CallbackQuery):
    await call.answer()
    await admin_create_invite(call.message, requester_id=call.from_user.id)


@router.callback_query(F.data == "m:qr")
async def cb_qr(call: CallbackQuery):
    # ✅ ищем vless:// в тексте сообщения, где была ссылка
    text = (call.message.text or call.message.caption or "")
    link = ""
    if "vless://" in text:
        link = text[text.find("vless://"):]
        for sep in ["\n", " ", "`"]:
            if sep in link:
                link = link.split(sep, 1)[0]
    if not link:
        await call.answer("Не нашёл ссылку рядом 😕 Нажми «Мой VPN» ещё раз.", show_alert=True)
        return

    await call.answer()
    await send_qr_photo(call.message, link)


# ✅ ДОБАВИЛ: кнопка "сбросить" → подтверждение
@router.callback_query(F.data == "m:reset")
async def cb_reset(call: CallbackQuery):
    await call.answer()
    await call.message.answer(
        "🔄 *Сбросить VPN?*\n\n"
        "Это выдаст *новую* ссылку/QR.\n"
        "Старая может перестать работать.\n\n"
        "Продолжаем?",
        parse_mode="Markdown",
        reply_markup=kb_confirm_reset(call.from_user.id),
    )


# ✅ ДОБАВИЛ: подтверждение сброса
@router.callback_query(F.data == "m:reset:yes")
async def cb_reset_yes(call: CallbackQuery):
    await call.answer("Сбрасываю…")
    await reset_my_vpn(call.message, call.from_user.id)


# =========================
# Invite FSM handler
# =========================
@router.message(Flow.waiting_invite)
async def invite_entered(message: Message, state: FSMContext):
    code = (message.text or "").strip().upper().replace(" ", "")
    if len(code) < 6:
        await message.answer("❗ Код слишком короткий. Введи ещё раз.", reply_markup=kb_back(message.from_user.id))
        return

    await state.clear()
    await message.answer("🔐 Проверяю код…", reply_markup=kb_back(message.from_user.id))
    await use_invite_and_send(message, code)


# =========================
# Entrypoint
# =========================
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
