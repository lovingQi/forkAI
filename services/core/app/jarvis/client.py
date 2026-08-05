"""JarvisClient：HTTP 代理 jarvis 车端（对齐 jarvis.ts，httpx.AsyncClient 实现）。

- get_state/get_map/get_params：非 2xx 或网络异常抛 JarvisError("jarvis state 500" 风格)。
- control：任何 HTTP 状态都尝试解析 JSON 返回（TS 版不抛错）；仅网络异常抛 JarvisError。
"""
import httpx


class JarvisError(Exception):
    pass


class JarvisClient:
    def __init__(self, cfg: dict):
        self._base = cfg["jarvis"]["baseUrl"].rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=3.0))

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
        """下发内联路线（UmWebSchedulerThis）。

        POST /api/control/schedulerthis，body 为 route JSON 展开（name + 节点键）。
        注意：HTTP 映射待真车验证（实施计划步骤21）；备选方案为
        /api/control/routes 命名路线方式（UmWebRoutes，需 routes.json 预置）。
        """
        return await self.control("schedulerthis", route)

    async def close(self) -> None:
        await self._client.aclose()
