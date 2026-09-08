"""
Login for the private dashboard — one account per team member (see users.json).

Sessions are a signed cookie (itsdangerous), so no server-side session store
is needed. The signing key lives in .env as SESSION_SECRET (auto-generated,
not something you need to fill in yourself).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import bcrypt
from itsdangerous import BadSignature, URLSafeTimedSerializer

USERS_FILE = Path(__file__).parent / "users.json"
SESSION_COOKIE_NAME = "prtracker_session"
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SESSION_SECRET = os.environ.get("SESSION_SECRET")


def _get_serializer() -> URLSafeTimedSerializer:
    if not SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET is missing from .env — run: python3 auth.py init"
        )
    return URLSafeTimedSerializer(SESSION_SECRET)


def load_users() -> list[dict]:
    return json.loads(USERS_FILE.read_text())["users"]


def verify_login(email: str, password: str) -> dict | None:
    email = email.strip().lower()
    for user in load_users():
        if user["email"].lower() == email:
            if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
                return user
    return None


def create_session_token(email: str) -> str:
    return _get_serializer().dumps({"email": email})


def read_session_token(token: str) -> dict | None:
    try:
        data = _get_serializer().loads(token, max_age=SESSION_MAX_AGE_SECONDS)
    except BadSignature:
        return None
    for user in load_users():
        if user["email"] == data.get("email"):
            return user
    return None


if __name__ == "__main__":
    import secrets
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "init":
        env_file = Path(__file__).parent / ".env"
        text = env_file.read_text() if env_file.exists() else ""
        if "SESSION_SECRET=" not in text:
            secret = secrets.token_hex(32)
            with env_file.open("a") as f:
                f.write(f"\nSESSION_SECRET={secret}\n")
            print("Added a new SESSION_SECRET to .env.")
        else:
            print("SESSION_SECRET already set in .env — left unchanged.")
    else:
        print("Usage: python3 auth.py init")
