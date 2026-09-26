"""统一错误码与业务异常（对齐 03-API 文档 §39）。"""

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
        """
        details = [
            {
                "type": error.get("type"),
                "loc": [str(part) for part in error.get("loc", ())],
                "msg": error.get("msg"),
                "input": (
                    None if error.get("input") is None else str(error.get("input"))
                ),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": ErrorCode.PARAM_ERROR,
                "message": "参数校验失败",
                "data": details,
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
