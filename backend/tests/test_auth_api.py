#!/usr/bin/env python3
"""Auth API tests — TestClient + a fake ICloudService (no real Apple calls).

Run:  cd backend && DATABASE_URL=... REDIS_URL=... python tests/test_auth_api.py
(env vars only needed so settings can load; the iCloud service is faked.)
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/icloud_sync")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.api import auth  # noqa: E402


class FakeICloud:
    def __init__(self) -> None:
        self._auth = False
        self._needs_2fa = False
        self.logged_out = False

    def get_status(self) -> dict:
        return {"authenticated": self._auth, "needs_2fa": self._needs_2fa}

    def authenticate(self, apple_id: str, password: str) -> bool:
        if password == "bad":
            raise RuntimeError("Invalid email/password combination.")
        self._needs_2fa = True
        return True

    def submit_2fa(self, code: str) -> bool:
        if code == "000000":
            return False
        self._needs_2fa = False
        self._auth = True
        return True

    def logout(self) -> None:
        self._auth = False
        self.logged_out = True

    # used only by the real lifespan restore; harmless here
    def try_restore(self) -> bool:
        return False


PASSED = 0


def check(label: str, cond: bool) -> None:
    global PASSED
    assert cond, f"FAILED: {label}"
    PASSED += 1
    print(f"  ✓ {label}")


def main_test() -> None:
    fake = FakeICloud()
    main.app.dependency_overrides[auth.get_service] = lambda: fake

    with TestClient(main.app) as client:
        print("== happy path ==")
        check("health ok", client.get("/api/health").json() == {"ok": True})

        r = client.get("/api/auth/status").json()
        check("initial status unauth", r == {"authenticated": False, "needs_2fa": False})

        check("login bad password → 401",
              client.post("/api/auth/login", json={"apple_id": "a@b.c", "password": "bad"}).status_code == 401)

        r = client.post("/api/auth/login", json={"apple_id": "a@b.c", "password": "good"})
        check("login good → requires_2fa true", r.json() == {"requires_2fa": True})
        check("status now needs_2fa", client.get("/api/auth/status").json()["needs_2fa"] is True)

        check("wrong 2fa → 400",
              client.post("/api/auth/2fa", json={"code": "000000"}).status_code == 400)

        r = client.post("/api/auth/2fa", json={"code": "123456"})
        check("correct 2fa → success", r.json() == {"success": True})
        check("status now authenticated", client.get("/api/auth/status").json()
              == {"authenticated": True, "needs_2fa": False})

        check("logout → success", client.post("/api/auth/logout").json() == {"success": True})
        check("logout cleared", fake.logged_out is True)
        check("status unauth after logout",
              client.get("/api/auth/status").json()["authenticated"] is False)

        print("== shared-secret gate ==")
        with patch("app.core.security.get_settings",
                   return_value=SimpleNamespace(api_shared_secret="s3cret")):
            check("no header → 401", client.get("/api/auth/status").status_code == 401)
            check("wrong header → 401",
                  client.get("/api/auth/status", headers={"X-Sync-Secret": "nope"}).status_code == 401)
            check("right header → 200",
                  client.get("/api/auth/status", headers={"X-Sync-Secret": "s3cret"}).status_code == 200)

    main.app.dependency_overrides.clear()
    print(f"\nALL {PASSED} CHECKS PASSED ✓")


if __name__ == "__main__":
    try:
        main_test()
    except AssertionError as e:
        print(f"\n{e}")
        sys.exit(1)
