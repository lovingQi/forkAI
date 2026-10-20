"""导出 robospace 可导入的工程 ZIP：project.json 以及 representations 里的文件。"""
import io
import json
import zipfile
from pathlib import Path

from ..errors import MappingError


def export_zip(directory: Path) -> bytes:
    project_path = directory / "project.json"
    if not project_path.is_file():
        raise MappingError("MAPPING_INPUT_INVALID", "地图不存在", status=404)
    project = json.loads(project_path.read_text(encoding="utf-8"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(project_path, "project.json")
        for item in project["representations"]:
            rel = item["path"]
            file_path = directory / rel
            if not file_path.is_file():
                raise MappingError("MAPPING_RESULT_INVALID", f"工程缺少 {rel}")
            archive.write(file_path, rel)
    return buf.getvalue()
