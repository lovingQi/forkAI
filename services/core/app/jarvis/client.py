"""JarvisClient：HTTP 代理 jarvis 车端（对齐 jarvis.ts，httpx.AsyncClient 实现）。

- get_state/get_map/get_params：非 2xx 或网络异常抛 JarvisError("jarvis state 500" 风格)。
- control：任何 HTTP 状态都尝试解析 JSON 返回（TS 版不抛错）；仅网络异常抛 JarvisError。
"""
import time

import httpx

from .station_names import extract_path_point_names

_MAP_NAMES_TTL_S = 60.0


class JarvisError(Exception):
    pass


class JarvisClient:
    def __init__(self, cfg: dict):
        self._base = cfg["jarvis"]["baseUrl"].rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))
        self._map_names: list[str] | None = None
        self._map_names_at = 0.0

    def _url(self, path: str) -> str:
        return f"{self._base}{path}"

    async def _get(self, path: str, label: str):
        try:
            res = await self._client.get(self._url(path))
        except httpx.HTTPError as e:
            raise JarvisError(str(e) or f"jarvis {label} unreachable") from e
        if not 200 <= res.status_code < 300:
            raise JarvisError(f"jarvis {label} {res.status_code}")
        return res.json()

    async def get_state(self):
        return await self._get("/api/state", "state")

    async def get_map(self):
        return await self._get("/api/map", "map")

    async def path_point_names(self) -> list[str]:
        """缓存地图 PathPoint 名；取图失败时返回上次成功结果或空列表。"""
        now = time.monotonic()
        if self._map_names is not None and (now - self._map_names_at) < _MAP_NAMES_TTL_S:
            return self._map_names
        try:
            names = extract_path_point_names(await self.get_map())
            self._map_names = names
            self._map_names_at = now
            return names
        except Exception:
            return list(self._map_names or [])

    async def get_params(self):
        return await self._get("/api/params", "params")

    async def control(self, action: str, payload: dict | None = None):
        try:
            res = await self._client.post(
                self._url(f"/api/control/{action}"), json=payload or {}
            )
        except httpx.HTTPError as e:
            raise JarvisError(str(e) or f"jarvis control {action} unreachable") from e
        try:
            return res.json()
        except Exception:
            return {}

    async def start_route(self, route: dict):
        """下发内联路线（JWebService::SchedulerThis）。

        POST /api/control/scheduler（src/service/JWebHttpServer.cpp:62 注册，
        :192 分发；JWebService.cpp:786-793：取 body.name → json["routes"]=name
        → mRoutes->Start(json)）。body 结构：{"name":..., "content":{"a":{cmd,...}}}
        （JRoutes::Start(JArg) 读 routes/key/id/content，JRoutes.h:45 注释；
        GetRKICFromArg 键名经 libgrm 反汇编确认）。
        备选：/api/control/routes 命名路线（body: routes/key/id）。
        """
        return await self.control("scheduler", route)

    async def close(self) -> None:
        await self._client.aclose()
