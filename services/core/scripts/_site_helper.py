"""回归脚本共用：用车载屏身份清掉现场锁。

手机端不能 force 抢锁之后，脚本若遇到上一轮残留的现场锁，先走本机口令清锁。
"""
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent


async def clear_site_via_kiosk(client, base: str = "http://127.0.0.1:19000") -> None:
    token = (CORE_ROOT / "data" / "console" / "kiosk.token").read_text(encoding="utf-8").strip()
    paired = await client.post(f"{base}/api/pair/local", json={"kioskToken": token})
    paired.raise_for_status()
    headers = {"Authorization": f"Bearer {paired.json()['pairToken']}"}
    ended = await client.post(f"{base}/api/site/end", headers=headers, json={"force": True})
    ended.raise_for_status()
