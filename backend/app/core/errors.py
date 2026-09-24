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
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "code": ErrorCode.PARAM_ERROR,
                "message": "参数校验失败",
                "data": exc.errors(),
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
