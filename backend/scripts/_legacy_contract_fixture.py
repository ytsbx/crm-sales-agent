"""隔离库历史合同夹具：旧模板接口已下线，历史归档/续签回归仍需创建旧文档。

只接管明确的旧模板创建请求，调用真实内部实现并验证真实登录权限；
新上传合同和其他 HTTP 请求仍由原套件访问实际 API。生产不导入此模块。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor

from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from _test_support import require_isolated_db


def legacy_contract_fixture(method, path, token, body):
    if method != 'POST' or not isinstance(body, dict):
        return None
    if path != '/contract-templates' and not (path == '/contract-documents' and body.get('template_id') and not body.get('original_file_id')):
        return None
    require_isolated_db()
    # 调用方可能已运行 asyncio；独立连接池和线程避免跨事件循环复用连接。
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(_create(path, token, body))).result()


async def _create(path, token, body):
    import app.main  # 注册模型，不启动后端或调度
    from app.core.config import settings
    from app.core.deps import ensure_permission, get_current_user
    from app.core.errors import AppError, ErrorCode
    from app.modules.contract import router, service
    from app.modules.contract.schema import DocumentCreate, TemplateCreate
    from app.core.response import ok
    _ = app.main
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            try:
                user = await get_current_user(HTTPAuthorizationCredentials(scheme='Bearer', credentials=token or ''), session)
                ensure_permission(user, 'order:manage')
                if path == '/contract-templates':
                    if not router._is_manager(user):
                        raise AppError(ErrorCode.FORBIDDEN, '模板夹具需要主管权限', 403)
                    template = await service.create_template(session, payload=TemplateCreate(**body), user_id=user.id)
                    await session.commit()
                    result = ok(service.serialize_template(template, is_current=True))
                else:
                    result = await router.create_document_record(DocumentCreate(**body),
                        Request({'type': 'http', 'headers': [], 'client': ('127.0.0.1', 1)}), user, session)
                return 200, jsonable_encoder(result)
            except AppError as error:
                await session.rollback()
                return error.http_status, {'code': error.code, 'message': error.message, 'data': None}
            except ValidationError as error:
                return 422, {'code': 40001, 'message': str(error), 'data': None}
    finally:
        await engine.dispose()
