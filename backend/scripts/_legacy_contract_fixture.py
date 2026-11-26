"""隔离库历史合同夹具：旧模板接口已下线，历史归档/续签回归仍需创建旧文档。

只接管明确的旧模板创建请求，调用真实内部实现并验证真实登录权限；
新上传合同和其他 HTTP 请求仍由原套件访问实际 API。生产不导入此模块。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from fastapi.encoders import jsonable_encoder
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    from app.core.data_scope import ensure_in_scope
    from app.core.deps import ensure_permission, get_current_user
    from app.core.errors import AppError, ErrorCode
    from app.core.response import ok
    from app.modules.contract import router, service
    from app.modules.contract.model import ContractDocument, ContractTemplate
    from app.modules.contract.schema import DocumentCreate
    from app.modules.customer import service as customer_service
    from app.modules.settings import numbering
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
                    template = await _legacy_template(session, body, user_id=user.id)
                    await session.commit()
                    result = ok(service.serialize_template(template, is_current=True))
                else:
                    result = await _legacy_document(
                        session, DocumentCreate(**body), body, user,
                        customer_service=customer_service,
                        ensure_in_scope=ensure_in_scope,
                        ContractDocument=ContractDocument,
                        ContractTemplate=ContractTemplate,
                        numbering=numbering,
                        ok=ok,
                    )
                    await session.commit()
                return 200, jsonable_encoder(result)
            except AppError as error:
                await session.rollback()
                return error.http_status, {'code': error.code, 'message': error.message, 'data': None}
            except ValidationError as error:
                await session.rollback()
                return 422, {'code': 40001, 'message': str(error), 'data': None}
    finally:
        await engine.dispose()


async def _legacy_template(session, body, *, user_id):
    """旧文本模板接口的隔离库替身：只给历史回归造模板，不接生产 HTTP。"""
    from app.modules.contract.model import ContractTemplate

    doc_type = str(body.get('doc_type') or 'contract')
    name = str(body.get('name') or '').strip()
    if not name:
        raise ValueError('name is required')
    template_body = str(body.get('body') or '')
    for _ in range(5):
        version = int(
            (
                await session.execute(
                    select(func.max(ContractTemplate.version)).where(
                        ContractTemplate.doc_type == doc_type,
                        ContractTemplate.name == name,
                        ContractTemplate.deleted_at.is_(None),
                    )
                )
            ).scalar()
            or 0
        ) + 1
        try:
            async with session.begin_nested():
                template = ContractTemplate(
                    doc_type=doc_type,
                    name=name,
                    version=version,
                    body=template_body,
                    enabled=True,
                    created_by=user_id,
                    created_at=datetime.now(UTC),
                )
                session.add(template)
                await session.flush()
            return template
        except IntegrityError:
            continue
    raise RuntimeError('模板版本号连续冲突')


async def _legacy_document(session, payload, raw_body, user, *, customer_service,
                           ensure_in_scope, ContractDocument, ContractTemplate,
                           numbering, ok):
    """旧模板生成合同的隔离库替身：保留权限/客户范围校验，正文只作历史快照。"""
    from app.modules.contract.service import serialize_document
    from app.modules.order.model import SalesOrder
    from app.modules.quote.model import Quote

    customer = await customer_service.get_visible_customer(session, user, payload.customer_id)
    await ensure_in_scope(session, user, owner_id=customer.owner_id, label='客户')
    order = await session.get(SalesOrder, payload.order_id) if payload.order_id else None
    if payload.order_id and order is None:
        from app.core.errors import AppError, ErrorCode
        raise AppError(ErrorCode.NOT_FOUND, '订单不存在', 404)
    if order is not None and order.customer_id != customer.id:
        from app.core.errors import AppError, ErrorCode
        raise AppError(ErrorCode.PARAM_ERROR, '所选订单不属于该客户', 422)
    if payload.order_id and order is not None:
        await ensure_in_scope(session, user, owner_id=order.owner_id, label='订单')
    quote = await session.get(Quote, payload.quote_id) if payload.quote_id else None
    if payload.quote_id and quote is None:
        from app.core.errors import AppError, ErrorCode
        raise AppError(ErrorCode.NOT_FOUND, '报价单不存在', 404)
    if quote is not None and quote.customer_id != customer.id:
        from app.core.errors import AppError, ErrorCode
        raise AppError(ErrorCode.PARAM_ERROR, '所选报价不属于该客户', 422)

    if payload.request_key:
        existing = (
            await session.execute(
                select(ContractDocument).where(
                    ContractDocument.request_key == payload.request_key,
                    ContractDocument.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.customer_id != customer.id:
                from app.core.errors import AppError, ErrorCode
                raise AppError(
                    ErrorCode.PARAM_ERROR, '该请求编号已用于另一份合同，请换一个编号', 409
                )
            return ok(serialize_document(existing, customer_name=customer.name))

    if payload.parent_id is not None:
        from app.modules.contract import router
        from app.core.errors import AppError, ErrorCode
        if not router._is_manager(user):
            raise AppError(ErrorCode.FORBIDDEN, '补充协议 / 续签需要主管权限', 403)
        if payload.effective_date is None:
            raise AppError(ErrorCode.REQUIRED_FIELD_MISSING, '续签必须填写生效日')
        if payload.expiry_date is not None and payload.effective_date > payload.expiry_date:
            raise AppError(ErrorCode.PARAM_ERROR, '生效日不能晚于到期日', 422)

    template = await session.get(ContractTemplate, payload.template_id)
    if template is None or template.deleted_at is not None:
        from app.core.errors import AppError, ErrorCode
        raise AppError(ErrorCode.NOT_FOUND, '模板不存在', 404)

    title = (payload.title or f'{customer.name}合同')[:200]
    filled_data = {'body': template.body, '_legacy_fixture': True}
    if payload.parent_id is not None:
        filled_data['_supersedes'] = {'id': payload.parent_id}
    doc = ContractDocument(
        doc_no=await numbering.generate_for(
            session, 'contract', model=ContractDocument, column=ContractDocument.doc_no
        ),
        doc_type=template.doc_type,
        title=title,
        customer_id=customer.id,
        order_id=order.id if order is not None else None,
        quote_id=quote.id if quote is not None else None,
        content_snapshot=str(raw_body.get('content_snapshot') or template.body or ''),
        filled_data=filled_data,
        expiry_date=payload.expiry_date,
        effective_date=payload.effective_date,
        parent_id=payload.parent_id,
        request_key=payload.request_key,
        created_by=user.id,
        created_at=datetime.now(UTC),
    )
    session.add(doc)
    await session.flush()
    return ok(serialize_document(doc, customer_name=customer.name))
