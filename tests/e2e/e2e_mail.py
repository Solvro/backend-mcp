import re
import time

import httpx


def verification_token(mailpit: httpx.Client, email: str, *, timeout: float = 30.0) -> str:
    """The token from the verification email auth-service sent to `email`."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listing = mailpit.get("/api/v1/messages", params={"limit": 50}).json()
        for message in listing.get("messages") or []:
            if not any(to["Address"] == email for to in message.get("To") or []):
                continue
            text = mailpit.get(f"/api/v1/message/{message['ID']}").json()["Text"]
            match = re.search(r"/auth/verify\?token=([^&\s]+)", text)
            if match:
                return match.group(1)
        time.sleep(0.5)
    raise AssertionError(f"no verification email for {email} within {timeout:.0f}s")
