"""车端参数查看、校验、整段下发、备份与恢复。"""
from ..config import now_ms
from ..jarvis.client import JarvisError

_TYPES = ("int", "double", "bool", "string")


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _type_ok(typ: str, value) -> bool:
    if typ == "bool":
        return isinstance(value, bool)
    if typ == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if typ == "double":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if typ == "string":
        return isinstance(value, str)
    return False


def _vel_moving(state: dict) -> bool:
    vel = state.get("vel")
    vx = vy = 0.0
    try:
        if isinstance(vel, str):
            parts = vel.split(",")
            vx = float(parts[0] or 0)
            vy = float(parts[1] or 0) if len(parts) > 1 else 0.0
        elif isinstance(vel, (list, tuple)) and vel:
            vx = float(vel[0] or 0)
            vy = float(vel[1] or 0) if len(vel) > 1 else 0.0
    except (TypeError, ValueError):
        return False
    return abs(vx) > 10 or abs(vy) > 10


class ParamsService:
    def __init__(self, cfg: dict, jarvis, store, sessions):
        self._cfg = cfg
        self._jarvis = jarvis
        self._store = store
        self._sessions = sessions
        params = (cfg.get("console") or {}).get("params") or {}
        self._editable_sections = set(params.get("editableSections") or [])

    async def _raw(self) -> dict:
        data = await self._jarvis.get_params()
        params = data.get("params") if isinstance(data, dict) else None
        return params if isinstance(params, dict) else {}

    def _item_editable(self, section: str, meta: dict) -> bool:
        return section in self._editable_sections and bool(str(meta.get("name_zh") or "").strip())

    async def get_view(self) -> dict:
        raw = await self._raw()
        sections = {}
        for section, items in raw.items():
            if not isinstance(items, dict):
                continue
            view_items = {}
            for key, meta in items.items():
                if not isinstance(meta, dict):
                    continue
                copied = dict(meta)
                copied["editable"] = self._item_editable(section, copied)
                view_items[key] = copied
            sections[section] = {
                "editable": section in self._editable_sections,
                "items": view_items,
            }
        return {"sections": sections}

    async def robot_moving(self) -> bool:
        state = await self._jarvis.get_state()
        if not isinstance(state, dict):
            return False
        if _vel_moving(state):
            return True
        mode = str(state.get("mode") or "")
        return mode not in ("Idle", "Stopped", "")

    def _validate(self, raw: dict, changes: dict) -> list:
        details = []
        if not isinstance(changes, dict) or not changes:
            details.append({"section": "", "key": "", "reason": "empty"})
            return details
        for section, items in changes.items():
            if section not in raw or not isinstance(raw.get(section), dict):
                details.append({"section": section, "key": "", "reason": "unknown_section"})
                continue
            if not isinstance(items, dict):
                details.append({"section": section, "key": "", "reason": "invalid_section"})
                continue
            for key, value in items.items():
                meta = raw[section].get(key)
                if not isinstance(meta, dict):
                    details.append({"section": section, "key": key, "reason": "unknown_key"})
                    continue
                if not self._item_editable(section, meta):
                    details.append({"section": section, "key": key, "reason": "readonly"})
                    continue
                typ = str(meta.get("type") or "")
                if typ not in _TYPES or not _type_ok(typ, value):
                    details.append({"section": section, "key": key, "reason": "type"})
                    continue
                ranges = meta.get("ranges") or []
                if typ in ("int", "double") and isinstance(ranges, list) and len(ranges) >= 2:
                    lo, hi = _num(ranges[0]), _num(ranges[1])
                    cur = _num(value)
                    if lo is None or hi is None or cur is None or cur < lo or cur > hi:
                        details.append({"section": section, "key": key, "reason": "range"})
        return details

    def _snapshot(self, raw: dict) -> dict:
        snap = {}
        for section, items in raw.items():
            if not isinstance(items, dict):
                continue
            snap[section] = {}
            for key, meta in items.items():
                if isinstance(meta, dict) and "default" in meta:
                    snap[section][key] = meta["default"]
        return snap

    def _merge(self, raw: dict, changes: dict) -> dict:
        merged = {}
        for section, items in changes.items():
            current = {}
            for key, meta in (raw.get(section) or {}).items():
                if isinstance(meta, dict) and "default" in meta:
                    current[key] = meta["default"]
            current.update(items)
            merged[section] = current
        return merged

    async def _write(self, payload: dict) -> None:
        try:
            res = await self._jarvis.post_config(payload)
        except JarvisError as e:
            raise JarvisError(str(e)) from e
        if isinstance(res, dict) and res.get("succeed") is False:
            raise JarvisError(str(res.get("error") or "config rejected"))

    async def apply(self, client_id: str, changes: dict, confirm: bool) -> dict:
        if not self._sessions.has_site(client_id):
            return {"ok": False, "status": 403, "error": "no_site"}
        if not confirm:
            return {"ok": False, "status": 400, "error": "confirm_required"}
        try:
            if await self.robot_moving():
                return {"ok": False, "status": 409, "error": "robot_moving"}
            raw = await self._raw()
        except JarvisError as e:
            return {"ok": False, "status": 502, "error": str(e)}
        details = self._validate(raw, changes)
        if details:
            return {"ok": False, "status": 400, "error": "invalid_params", "details": details}
        before = self._snapshot(raw)
        merged = self._merge(raw, changes)
        backup_id = self._store.add_backup(client_id, {k: before.get(k, {}) for k in merged})
        try:
            await self._write(merged)
        except JarvisError as e:
            return {"ok": False, "status": 502, "error": str(e), "backupId": backup_id}
        self._store.append_log(
            {
                "ts": now_ms(),
                "clientId": client_id,
                "action": "apply",
                "changes": changes,
                "backupId": backup_id,
            }
        )
        return {"ok": True, "backupId": backup_id}

    async def restore(self, client_id: str, backup_id: str, confirm: bool) -> dict:
        if not self._sessions.has_site(client_id):
            return {"ok": False, "status": 403, "error": "no_site"}
        if not confirm:
            return {"ok": False, "status": 400, "error": "confirm_required"}
        saved = self._store.read_backup(backup_id)
        if not saved or not isinstance(saved.get("values"), dict):
            return {"ok": False, "status": 400, "error": "invalid_params", "details": [{"section": "", "key": "", "reason": "backup"}]}
        try:
            if await self.robot_moving():
                return {"ok": False, "status": 409, "error": "robot_moving"}
            raw = await self._raw()
            current_backup = self._store.add_backup(client_id, self._snapshot(raw))
            await self._write(saved["values"])
        except JarvisError as e:
            return {"ok": False, "status": 502, "error": str(e)}
        self._store.append_log(
            {
                "ts": now_ms(),
                "clientId": client_id,
                "action": "restore",
                "backupId": backup_id,
                "currentBackupId": current_backup,
            }
        )
        return {"ok": True, "backupId": backup_id, "currentBackupId": current_backup}

    def list_backups(self) -> list:
        return self._store.list_backups()

    def read_log(self) -> list:
        return self._store.read_log()
