"""控制台落盘：向导标记、车载屏口令、参数备份、修改日志。"""
import json
import os
import re
import secrets
import uuid
from pathlib import Path

from ..config import CORE_ROOT, now_ms

_BACKUP_ID = re.compile(r"^[0-9]+-[0-9a-f]+$")


class ConsoleStore:
    def __init__(self, cfg: dict):
        console = cfg.get("console") or {}
        root = Path(str(console.get("dataDir") or "data/console"))
        if not root.is_absolute():
            root = CORE_ROOT / root
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._backup_dir = self.root / "params_backups"
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._keep = int((console.get("params") or {}).get("backupKeep", 50))
        self._ensure_kiosk()

    def _atomic(self, path: Path, obj) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    def _ensure_kiosk(self) -> None:
        path = self.root / "kiosk.token"
        if not path.is_file():
            path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def kiosk_token(self) -> str:
        return (self.root / "kiosk.token").read_text(encoding="utf-8").strip()

    def setup_state(self) -> dict:
        path = self.root / "setup.json"
        if not path.is_file():
            return {"completed": False}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"completed": False}
        return {"completed": bool(data.get("completed")), "completedAt": data.get("completedAt") or 0}

    def complete_setup(self) -> dict:
        state = {"completed": True, "completedAt": now_ms()}
        self._atomic(self.root / "setup.json", state)
        return state

    def reset_setup(self) -> dict:
        state = {"completed": False, "completedAt": 0}
        self._atomic(self.root / "setup.json", state)
        return state

    def add_backup(self, client_id: str, values: dict) -> str:
        bid = f"{now_ms()}-{uuid.uuid4().hex[:8]}"
        payload = {"id": bid, "ts": now_ms(), "clientId": client_id, "values": values}
        self._atomic(self._backup_dir / f"{bid}.json", payload)
        self._prune()
        return bid

    def _prune(self) -> None:
        files = sorted(self._backup_dir.glob("*.json"))
        extra = len(files) - self._keep
        for path in files[: max(0, extra)]:
            try:
                path.unlink()
            except OSError:
                pass

    def list_backups(self) -> list:
        items = []
        for path in sorted(self._backup_dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            items.append({"id": data.get("id") or path.stem, "ts": data.get("ts") or 0, "clientId": data.get("clientId") or ""})
        return items

    def read_backup(self, backup_id: str) -> dict | None:
        if not _BACKUP_ID.match(str(backup_id or "")):
            return None
        path = self._backup_dir / f"{backup_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def append_log(self, record: dict) -> None:
        path = self.root / "params_changes.jsonl"
        line = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_log(self, limit: int = 200) -> list:
        path = self.root / "params_changes.jsonl"
        if not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        out = []
        for line in lines[-max(1, int(limit)) :]:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                out.append(item)
        return out
