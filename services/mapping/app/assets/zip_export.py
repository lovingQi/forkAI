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
            if file_path.is_dir():
                files = [path for path in file_path.rglob("*") if path.is_file()]
                if not files:
                    raise MappingError("MAPPING_RESULT_INVALID", f"工程缺少 {rel}")
                for path in files:
                    archive.write(path, (Path(rel) / path.relative_to(file_path)).as_posix())
            elif file_path.is_file():
                archive.write(file_path, rel)
            else:
                raise MappingError("MAPPING_RESULT_INVALID", f"工程缺少 {rel}")
    return buf.getvalue()
