"""产品错误码。引擎内部细节只进诊断，不进普通界面文案。"""
import uuid

CODES = (
    "MAPPING_ENGINE_UNAVAILABLE",
    "MAPPING_ENGINE_INCOMPATIBLE",
    "MAPPING_INPUT_INVALID",
    "MAPPING_CALIBRATION_MISSING",
    "MAPPING_TASK_BUSY",
    "MAPPING_TASK_START_FAILED",
    "MAPPING_TASK_INTERRUPTED",
    "MAPPING_RESULT_INVALID",
)

_STATUS = {
    "MAPPING_ENGINE_UNAVAILABLE": 503,
    "MAPPING_ENGINE_INCOMPATIBLE": 409,
    "MAPPING_INPUT_INVALID": 400,
    "MAPPING_CALIBRATION_MISSING": 400,
    "MAPPING_TASK_BUSY": 409,
    "MAPPING_TASK_START_FAILED": 500,
    "MAPPING_TASK_INTERRUPTED": 409,
    "MAPPING_RESULT_INVALID": 500,
}


class MappingError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None, diagnostic: str | None = None):
        if code not in CODES:
            raise ValueError(code)
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = _STATUS[code] if status is None else status
        self.diagnostic = diagnostic or message
        self.diagnostic_id = uuid.uuid4().hex[:12]

    def body(self) -> dict:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "diagnosticId": self.diagnostic_id,
            }
        }
