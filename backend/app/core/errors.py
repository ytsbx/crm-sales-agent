"""统一错误码与业务异常（对齐 03-API 文档 §39）。"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger("crm.errors")

from starlette.exceptions import HTTPException as StarletteHTTPException


class ErrorCode:
    PARAM_ERROR = 40001
    STATUS_NOT_ALLOWED = 40002
    REQUIRED_FIELD_MISSING = 40003
    UNAUTHORIZED = 40101
    TOKEN_EXPIRED = 40102
    FORBIDDEN = 40301
    DATA_SCOPE_DENIED = 40302
    PRICE_PERMISSION_DENIED = 40303
    NOT_FOUND = 40401
    DUPLICATE = 40901
    VERSION_CONFLICT = 40902
    DUPLICATE_CONVERT = 40903
    PRICE_TOO_LOW = 42201
    APPROVAL_REQUIRED = 42202
    APPROVAL_PENDING = 42203
    QUOTE_VERSION_LOCKED = 42204
    # D7：低于绝对底价。与 PRICE_TOO_LOW（可审批的低价）本质区别：
    # 这个是硬拒绝，任何审批角色都不能通过，不生成审批单
    PRICE_BELOW_HARD_FLOOR = 42205
    RATE_LIMITED = 42901
    SYSTEM_ERROR = 50001
    EXTERNAL_ERROR = 50201
    WECOM_SYNC_FAILED = 50202
    ERP_SYNC_FAILED = 50203


class AppError(Exception):
    """业务异常：抛出后由全局处理器转成统一响应结构。"""

    def __init__(self, code: int, message: str, http_status: int = status.HTTP_400_BAD_REQUEST):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


#: pydantic 的错误类型 → 一句**给用户看**的人话（`ctx` 里带着约束值，能填进去）。
#:
#: 为什么需要（第十二批收尾）：从前这类错误一律回「参数校验失败」，前端再把字段名
#: （英文 key）拼在后面 —— 用户看到的是「参数校验失败：quantity」。而其中两类
#: 最要命的数值问题（数字太大、小数位太多）**在加约束之前根本走不到这里**，
#: 是撞库报的 **500「服务器内部错误」**，用户完全不知道是自己填大了。
#: 加完约束之后它们变成 400，但「参数校验失败」仍等于什么都没说。
#:
#: 只登记"用户真的会碰到、且英文原文看不懂"的那些；没登记的退回 pydantic 原文
#: （英文），总比空着强。
_VALIDATION_HINTS: dict[str, str] = {
    "greater_than": "必须大于 {gt}",
    "greater_than_equal": "不能小于 {ge}",
    "less_than": "必须小于 {lt}",
    "less_than_equal": "不能大于 {le}",
    "decimal_max_digits": "这个数太大，超出系统能记的范围",
    "decimal_max_places": "小数位太多（最多保留 {decimal_places} 位小数）",
    "string_too_long": "内容太长（最多 {max_length} 个字）",
    "string_too_short": "内容太短（至少 {min_length} 个字）",
    "missing": "这一项是必填的",
    "int_parsing": "这一项要填数字",
    "decimal_parsing": "这一项要填数字",
    "float_parsing": "这一项要填数字",
    "date_parsing": "这一项要填日期",
    "datetime_parsing": "这一项要填日期时间",
    "bool_parsing": "这一项只能填「是」或「否」",
}

#: 位置前缀对使用者没意义，读的时候跳过。
_LOCATION_PREFIXES = {"body", "query", "path", "header", "cookie"}


def _field_label(field: str) -> str:
    """给字段名配一个中文称呼。

    复用 `patch_schema.FIELD_LABELS` —— 项目里**唯一**一张字段中文名表，
    按"以 `.字段名` 结尾"匹配（那张表的键是「类名.字段名」）。
    没登记的退回字段名本身：英文 key 也比一句笼统的话强。
    """
    if not field:
        return ""
    try:
        from app.core.patch_schema import FIELD_LABELS
    except Exception:  # 拿不到就退化成英文，不影响校验本身
        return field
    suffix = f".{field}"
    for key, label in FIELD_LABELS.items():
        if key.endswith(suffix):
            return label
    return field


def _humanize(error: dict) -> str | None:
    """把一条 pydantic 错误翻成「哪一项 + 怎么了」；认不出就返回 None。

    不认识的类型不硬编 —— 宁可退回原文，也不要编一句可能不对的话。
    """
    # schema 主动给出的业务提示（包括 PatchModel 的非空列提示）应直接展示。
    # 不把原始入参拼进提示，且只接受验证器产生的 ValueError。
    if error.get("type") == "value_error":
        cause = (error.get("ctx") or {}).get("error")
        if isinstance(cause, ValueError):
            return str(cause) or None
    template = _VALIDATION_HINTS.get(str(error.get("type")))
    if not template:
        return None
    ctx = error.get("ctx") or {}
    try:
        reason = template.format(**ctx)
    except (KeyError, IndexError):
        reason = template
    location = [str(part) for part in error.get("loc") or ()]
    field = next(
        (part for part in reversed(location)
         if part not in _LOCATION_PREFIXES and not part.isdigit()),
        "",
    )
    label = _field_label(field)
    return f"「{label}」{reason}" if label else reason


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"code": exc.code, "message": exc.message, "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """参数校验失败。

        **注意 `exc.errors()` 不能直接塞进 JSONResponse**：Pydantic v2 会把原始
        输入放在每个错误的 `input` 字段里，如果那个输入是 `Decimal` / `date` /
        自定义类型，`json.dumps` 会抛 `TypeError` —— 异常处理器自己抛异常，
        结果是 **500 而不是 400**。

        实测：`{"rate": 0}` 打到带 `Field(gt=0)` 的接口会 500。
        任何带数值约束的入参（负数量、0 价格、超范围比率…）校验失败都会中招。

        所以这里统一把错误明细转成"一定能序列化"的结构：
        input 用 str() 呈现，够定位问题，也不会再炸。

        `message` 另外做一件事：**认得出的错误类型翻成一句人话**（见
        `_VALIDATION_HINTS`），例如数量填了 1e17 会得到
        「「数量」这个数太大，超出系统能记的范围」，而不是笼统的「参数校验失败」。
        认不出的类型保持原样，前端会把字段名拼在后面 —— 旧行为不变。
        """
        details = [
            {
                "type": error.get("type"),
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": error.get("msg"),
                "hint": _humanize(error),
                "input": (
                    None if error.get("input") is None else str(error.get("input"))
                ),
            }
            for error in exc.errors()
        ]
        first_hint = next((item["hint"] for item in details if item["hint"]), None)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": ErrorCode.PARAM_ERROR,
                "message": first_hint or "参数校验失败",
                "data": details,
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """未捕获异常的兜底：统一信封 + 完整堆栈进日志。

        此前模型调用失败这类裸异常会走 Starlette 默认 500 纯文本，
        前端只能显示"网络异常"，排查没有 code 可查。
        """
        logger.exception("未处理异常：%s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": ErrorCode.SYSTEM_ERROR,
                "message": "服务器内部错误，请稍后重试或联系管理员",
                "data": None,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = ErrorCode.UNAUTHORIZED if exc.status_code == 401 else ErrorCode.NOT_FOUND
        if exc.status_code == 403:
            code = ErrorCode.FORBIDDEN
        elif exc.status_code >= 500:
            code = ErrorCode.SYSTEM_ERROR
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": code, "message": str(exc.detail), "data": None},
        )
