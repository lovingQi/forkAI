"""二维码里使用的主机地址。"""
import socket


def _loopback(ip: str) -> bool:
    text = (ip or "").strip().lower()
    return text in ("", "127.0.0.1", "::1", "localhost", "0.0.0.0")


def _udp_host() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()
    return "" if _loopback(ip) else ip


async def resolve_public_host(cfg: dict, jarvis) -> str:
    console = cfg.get("console") or {}
    configured = str(console.get("publicHost") or "").strip()
    if configured:
        return configured
    try:
        state = await jarvis.get_state()
    except Exception:
        state = {}
    ip = str((state or {}).get("ip") or "").strip()
    if not _loopback(ip):
        return ip
    probed = _udp_host()
    if probed:
        return probed
    return "127.0.0.1"
