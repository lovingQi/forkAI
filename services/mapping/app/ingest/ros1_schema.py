"""按消息定义动态注册 ROS1 类型，用来解 Livox CustomMsg 这类非标准消息。"""
from rosbags.typesys import Stores, get_typestore
from rosbags.typesys.msg import get_types_from_msg

from ..errors import MappingError

_store = None


def _typestore():
    global _store
    if _store is None:
        _store = get_typestore(Stores.ROS1_NOETIC)
    return _store


def _ros_name(schema_name: str) -> str:
    name = schema_name.strip()
    if "/msg/" in name:
        return name
    if "/" not in name:
        raise MappingError("MAPPING_INPUT_INVALID", f"消息类型无效：{schema_name}")
    pkg, rest = name.split("/", 1)
    return f"{pkg}/msg/{rest}"


def register(schema_name: str, msgdef_text: str) -> None:
    """登记一条消息定义。多段定义用 'MSG:' 分隔，依赖类型要先出现。"""
    if not msgdef_text or not msgdef_text.strip():
        return
    chunks = []
    current_name = _ros_name(schema_name)
    body: list[str] = []
    for line in msgdef_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("MSG:"):
            if body:
                chunks.append((current_name, "\n".join(body)))
            current_name = _ros_name(stripped.split(":", 1)[1].strip())
            body = []
        elif stripped.startswith("===="):
            continue
        else:
            body.append(line)
    if body:
        chunks.append((current_name, "\n".join(body)))
    store = _typestore()
    for name, text in chunks:
        try:
            store.register(get_types_from_msg(text, name))
        except Exception:
            # 标准类型已经在 noetic 库里。
            continue


def decode(schema_name: str, payload: bytes):
    typename = _ros_name(schema_name)
    try:
        return _typestore().deserialize_ros1(payload, typename)
    except Exception as exc:
        raise MappingError("MAPPING_INPUT_INVALID", f"无法解码 {schema_name}") from exc


def field(msg, name: str, default=None):
    return getattr(msg, name, default)
