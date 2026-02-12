import os
import requests

XUI_BASE_URL = os.getenv("XUI_BASE_URL")
XUI_USERNAME = os.getenv("XUI_USERNAME")
XUI_PASSWORD = os.getenv("XUI_PASSWORD")
XUI_INBOUND_ID = os.getenv("XUI_INBOUND_ID")

session = requests.Session()

def login():
    session.post(f"{XUI_BASE_URL}/login", data={
        "username": XUI_USERNAME,
        "password": XUI_PASSWORD
    })

def add_client(uuid):
    payload = {
        "id": int(XUI_INBOUND_ID),
        "settings": {
            "clients": [{
                "id": uuid,
                "flow": "xtls-rprx-vision",
                "email": uuid,
                "enable": True
            }]
        }
    }
    session.post(f"{XUI_BASE_URL}/panel/api/inbounds/addClient", json=payload)

def create_vpn(uuid):
    login()
    add_client(uuid)
