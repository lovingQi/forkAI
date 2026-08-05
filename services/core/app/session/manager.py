"""SessionManager：配对码 / Token / 现场锁 / 唤醒武装（1:1 对齐 session.ts）。

注意：站点会话 dict 直接使用 camelCase 键（siteSessionId/holderClientId/expiresAt），
与 TS 版 JSON 线格式保持一致。
"""
import secrets
import uuid

from ..config import now_ms


def _gen_code() -> str:
    # JS randomInt(100000, 999999)：6 位数字
    return str(100000 + secrets.randbelow(899999))


class SessionManager:
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._pair_pending = None  # {"code": str, "expiresAt": int} | None
        self._tokens: dict[str, dict] = {}  # pairToken -> {"clientId","expiresAt"}
        self._site = None  # {"siteSessionId","holderClientId","expiresAt"} | None
        self._site_nonce = str(uuid.uuid4())
        self._site_code = _gen_code()
        self._wake_armed_until = 0
        self._wake_client_id = None

    # ---- 配对 ----
    def start_pair(self) -> dict:
        self._pair_pending = {
            "code": _gen_code(),
            "expiresAt": now_ms() + self._cfg["pairCodeTtlMs"],
        }
        return self._pair_pending

    def get_pair_pending(self):
        if self._pair_pending and self._pair_pending["expiresAt"] < now_ms():
            self._pair_pending = None
        return self._pair_pending

    def confirm_pair(self, code: str):
        pending = self.get_pair_pending()
        if not pending or pending["code"] != code:
            return None
        client_id = str(uuid.uuid4())
        pair_token = str(uuid.uuid4())
        expires_at = now_ms() + self._cfg["pairTokenTtlMs"]
        self._tokens[pair_token] = {"clientId": client_id, "expiresAt": expires_at}
        self._pair_pending = None
        return {"pairToken": pair_token, "expiresAt": expires_at, "clientId": client_id}

    def resolve_token(self, pair_token):
        if not pair_token:
            return None
        rec = self._tokens.get(pair_token)
        if not rec:
            return None
        if rec["expiresAt"] < now_ms():
            del self._tokens[pair_token]
            return None
        return rec

    # ---- 现场锁 ----
    def rotate_site_codes(self) -> None:
        self._site_nonce = str(uuid.uuid4())
        self._site_code = _gen_code()

    def get_site_public(self) -> dict:
        self._ensure_site_valid()
        return {
            "vehicleId": self._cfg["vehicleId"],
            "nonce": self._site_nonce,
            "code": self._site_code,
            "session": dict(self._site) if self._site else None,
        }

    def _ensure_site_valid(self) -> None:
        if self._site and self._site["expiresAt"] < now_ms():
            self._site = None

    def unlock_site(self, client_id: str, nonce=None, code=None, force=False) -> dict:
        self._ensure_site_valid()
        ok_nonce = bool(nonce) and nonce == self._site_nonce
        ok_code = bool(code) and code == self._site_code
        if not ok_nonce and not ok_code:
            return {"ok": False, "error": "invalid"}

        if self._site and self._site["holderClientId"] != client_id:
            if not force:
                return {
                    "ok": False,
                    "error": "held",
                    "holderClientId": self._site["holderClientId"],
                }

        stolen = bool(self._site and self._site["holderClientId"] != client_id)
        self._site = {
            "siteSessionId": str(uuid.uuid4()),
            "holderClientId": client_id,
            "expiresAt": now_ms() + self._cfg["siteSessionTtlMs"],
        }
        self.rotate_site_codes()
        return {"ok": True, "session": self._site, "stolen": stolen}

    def end_site(self, client_id: str, force: bool = False) -> bool:
        self._ensure_site_valid()
        if not self._site:
            return True
        if not force and self._site["holderClientId"] != client_id:
            return False
        self._site = None
        return True

    def has_site(self, client_id: str) -> bool:
        self._ensure_site_valid()
        return bool(self._site and self._site["holderClientId"] == client_id)

    def has_any_site(self) -> bool:
        self._ensure_site_valid()
        return bool(self._site)

    def get_site_holder(self):
        self._ensure_site_valid()
        return self._site["holderClientId"] if self._site else None

    # ---- 唤醒武装 ----
    def arm_wake(self, client_id: str) -> None:
        self._wake_armed_until = now_ms() + self._cfg["wakeArmMs"]
        self._wake_client_id = client_id

    def is_wake_armed(self, client_id: str) -> bool:
        if now_ms() > self._wake_armed_until:
            self._wake_armed_until = 0
            self._wake_client_id = None
            return False
        return self._wake_client_id == client_id

    def clear_wake_arm(self) -> None:
        self._wake_armed_until = 0
        self._wake_client_id = None
