import os
import json
import requests

XUI_BASE_URL = os.getenv("XUI_BASE_URL", "").rstrip("/")
XUI_USERNAME = os.getenv("XUI_USERNAME", "")
XUI_PASSWORD = os.getenv("XUI_PASSWORD", "")
XUI_INBOUND_ID = int(os.getenv("XUI_INBOUND_ID", "1"))

session = requests.Session()


def _req(method: str, url: str, **kwargs):
    return session.request(method, url, timeout=10, **kwargs)


def login():
    r = _req(
        "POST",
        f"{XUI_BASE_URL}/login",
        data={"username": XUI_USERNAME, "password": XUI_PASSWORD},
    )
    if r.status_code != 200:
        raise Exception(f"Login HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    if not j.get("success"):
        raise Exception(f"Login failed: {j}")


def add_client(uuid: str):
    """
    ТОЛЬКО рабочий путь для твоей сборки:
      POST /panel/api/inbounds/addClient
    Никаких update endpoints (у тебя 404).
    """
    settings = {
        "clients": [
            {
                "id": uuid,
                "flow": "xtls-rprx-vision",
                "email": uuid,
                "enable": True,
            }
        ]
    }

    payload = {
        "id": XUI_INBOUND_ID,
        "settings": json.dumps(settings, ensure_ascii=False),
    }

    r = _req("POST", f"{XUI_BASE_URL}/panel/api/inbounds/addClient", json=payload)
    if r.status_code != 200:
        raise Exception(f"addClient HTTP {r.status_code}: {r.text[:400]}")

    try:
        j = r.json()
        if j.get("success") is False:
            raise Exception(f"addClient failed: {j}")
    except Exception:
        # если ответ не JSON, но 200 — не валимся
        pass


def delete_client(uuid: str):
    """
    Самая стабильная ручка удаления (у тебя она работает):
      POST /panel/api/inbounds/{id}/delClient/{uuid}
    """
    url = f"{XUI_BASE_URL}/panel/api/inbounds/{XUI_INBOUND_ID}/delClient/{uuid}"
    r = _req("POST", url)
    if r.status_code != 200:
        raise Exception(f"delClient HTTP {r.status_code}: {r.text[:400]}")
    try:
        j = r.json()
        if j.get("success") is False:
            raise Exception(f"delClient failed: {j}")
    except Exception:
        pass


def create_vpn(uuid: str):
    login()
    add_client(uuid)


def remove_vpn(uuid: str):
    """
    Старая логика удаления оставлена, но приоритетно используем delClient/{uuid}.
    """
    login()

    # 1) самый правильный и стабильный вариант
    try:
        delete_client(uuid)
        return
    except Exception:
        pass

    # 2) fallback на “плавающие” ручки (если вдруг появятся)
    candidates = [
        ("/panel/api/inbounds/delClient", {"id": XUI_INBOUND_ID, "clientId": uuid}),
        ("/panel/api/inbounds/delClient", {"clientId": uuid}),
        ("/panel/api/inbounds/removeClient", {"id": XUI_INBOUND_ID, "clientId": uuid}),
        ("/panel/api/inbounds/removeClient", {"clientId": uuid}),
    ]

    last_err = None
    for path, payload in candidates:
        r = _req("POST", f"{XUI_BASE_URL}{path}", json=payload)
        if r.status_code != 200:
            last_err = f"{path} HTTP {r.status_code}: {r.text[:200]}"
            continue
        try:
            j = r.json()
            if j.get("success") is False:
                last_err = f"{path} failed: {j}"
                continue
        except Exception:
            pass
        return

    raise Exception(last_err or "remove client failed")


def reset_vpn(old_uuid: str, new_uuid: str):
    """
    RESET без update:
      - пытаемся удалить старый (если не удалился — не валимся)
      - добавляем новый (это главное)
    """
    login()

    if old_uuid:
        try:
            delete_client(old_uuid)
        except Exception:
            pass

    add_client(new_uuid)
