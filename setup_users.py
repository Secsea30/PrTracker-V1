"""
One-time setup: creates users.json with a temporary password for each team
member. Run once:
    python3 setup_users.py

Prints the temporary passwords ONCE so you can relay them to each person
(Slack, in person, however you'd share a temp password normally). They
aren't stored anywhere in plain text — only bcrypt hashes go into users.json.
"""

import json
import secrets
import string
from pathlib import Path

import bcrypt

USERS_FILE = Path(__file__).parent / "users.json"

TEAM = [
    {"name": "Antony", "email": "antony@placecomms.com"},
    {"name": "Rad", "email": "rad@placecomms.com"},
    {"name": "Mohanad", "email": "mohanad@placecomms.com"},
    {"name": "Bana", "email": "bana@placecomms.com"},
]


def generate_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(10))


def main():
    if USERS_FILE.exists():
        print("users.json already exists — not overwriting. Delete it first if you want to start over.")
        return

    users = []
    print("Temporary passwords (share these with each person, then they should be changed):\n")
    for person in TEAM:
        temp_password = generate_temp_password()
        password_hash = bcrypt.hashpw(temp_password.encode(), bcrypt.gensalt()).decode()
        users.append({
            "name": person["name"],
            "email": person["email"],
            "password_hash": password_hash,
        })
        print(f"  {person['name']:<10} {person['email']:<25} temp password: {temp_password}")

    USERS_FILE.write_text(json.dumps({"users": users}, indent=2))
    print(f"\nSaved to {USERS_FILE.name} (hashed passwords only).")


if __name__ == "__main__":
    main()
