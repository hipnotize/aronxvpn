import os
import json
import requests

XUI_BASE_URL = os.getenv("XUI_BASE_URL", "").rstrip("/")
XUI_USERNAME = os.getenv("XUI_USERNAME", "")
XUI_PASSWORD = os.getenv("XUI_PASSWORD", "")
XUI_INBOUND_ID = int(os.getenv("XUI_INBOUND_ID", "1"))

session = requests.Session()

def login():
    r = session.post(
        f"{XUI_BASE_URL}/login",
        data={"username": XUI_USERNAME, "password": XUI_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        raise Exception(f"Login HTTP {r.status_code}: {r.text[:200]}")
    j = r.json()
    if not j.get("success"):
        raise Exception(f"Login failed: {j}")

def add_client(uuid: str):
    settings = {"clients": [{
        "id": uuid,
        "flow": "xtls-rprx-vision",
        "email": uuid,
        "enable": True
    }]}

    payload = {
        "id": XUI_INBOUND_ID,
        "settings": json.dumps(settings)
    }

    r = session.post(
        f"{XUI_BASE_URL}/panel/api/inbounds/addClient",
        json=payload,
        timeout=10,
    )
    if r.status_code != 200:
        raise Exception(f"Add client HTTP {r.status_code}: {r.text[:400]}")

    try:
        j = r.json()
        if not j.get("success"):
            raise Exception(f"Add client failed: {j}")
    except Exception:
        pass

def delete_client(uuid: str):
    # 3x-ui: POST /panel/api/inbounds/:id/delClient/:clientId  (clientId = uuid для VLESS)  [oai_citation:1‡hub.docker.com](https://hub.docker.com/r/aircross/3x-ui)
    url = f"{XUI_BASE_URL}/panel/api/inbounds/{XUI_INBOUND_ID}/delClient/{uuid}"
    r = session.post(url, timeout=10)
    if r.status_code != 200:
        raise Exception(f"Del client HTTP {r.status_code}: {r.text[:400]}")
    try:
        j = r.json()
        if not j.get("success"):
            raise Exception(f"Del client failed: {j}")
    except Exception:
        pass

def create_vpn(uuid: str):
    login()
    add_client(uuid)

def remove_vpn(uuid: str):
    login()

    # В 3x-ui обычно endpoint такой:
    # POST /panel/api/inbounds/delClient  (иногда removeClient)
    # и принимает {"id": INBOUND_ID, "clientId": "<uuid>"} либо {"clientId": "<uuid>"}.
    # Сделаем попытки по распространённым вариантам.

    candidates = [
        ("/panel/api/inbounds/delClient", {"id": XUI_INBOUND_ID, "clientId": uuid}),
        ("/panel/api/inbounds/delClient", {"clientId": uuid}),
        ("/panel/api/inbounds/removeClient", {"id": XUI_INBOUND_ID, "clientId": uuid}),
        ("/panel/api/inbounds/removeClient", {"clientId": uuid}),
    ]

    last_err = None
    for path, payload in candidates:
        r = session.post(f"{XUI_BASE_URL}{path}", json=payload, timeout=10)
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
        return  # успех

    raise Exception(last_err or "remove client failed")
