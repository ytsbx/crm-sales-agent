"""FastAPI 入口。"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.response import ok
from app.modules.approval.router import router as approval_router
from app.modules.approval.rules_router import router as approval_rules_router
from app.modules.analytics.router import router as analytics_router
from app.modules.agent.router import router as agent_router
from app.modules.auth.router import router as auth_router
from app.modules.cases.router import router as cases_router
from app.modules.audit_router import router as audit_router
from app.modules.contract.router import router as contract_router
from app.modules.customer.router import router as customer_router
from app.modules.customer.contacts_router import router as contacts_router
from app.modules.customer.io_router import router as customer_io_router
from app.modules.customer.tags_router import router as customer_tags_router
from app.modules.erp.router import router as erp_router
from app.modules.followup.router import router as followup_router
from app.modules.inquiry.router import router as inquiry_router
from app.modules.file.router import router as file_router
from app.modules.lead.router import router as lead_router
from app.modules.lead.io_router import router as lead_io_router
from app.modules.opportunity.router import router as opportunity_router
from app.modules.order.router import router as order_router
from app.modules.notification.router import router as notification_router
from app.modules.payment.router import router as payment_router
from app.modules.pricing.io_router import router as pricing_io_router
from app.modules.pricing.logistics_router import router as logistics_router
from app.modules.pricing.router import router as pricing_router
from app.modules.product.router import router as product_router
from app.modules.product.io_router import router as product_io_router
from app.modules.public_pool.router import router as public_pool_router
from app.modules.quote.router import router as quote_router
from app.modules.sample.router import router as sample_router
from app.modules.task.router import router as task_router
from app.modules.timeline.router import router as timeline_router
from app.modules.user.router import router as user_router
from app.modules.settings.router import router as settings_router
from app.modules.search.router import router as search_router
from app.modules.wecom.router import router as wecom_router

# 应用日志（含 crm.scheduler 的 INFO）：uvicorn 只配它自己的 logger，
# 不加这个，应用侧 INFO 日志（如"定时任务已启动"）会静默丢弃
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 调度器要等事件循环就绪（AsyncIOScheduler 挂在应用自己的 loop 上）
    from app.core.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    lifespan=lifespan,
    title=f"{settings.app_name} API",
    version="1.1.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth_router, prefix=settings.api_prefix)
# 注意顺序：/customers/import-template、/customers/export 必须注册在 /customers/{id} 之前，
# 否则会被动态路由抢先匹配（这个坑在 /products/for-pricing 上踩过一次）
app.include_router(customer_io_router, prefix=settings.api_prefix)
# 同理：/tags、/customers/deduplicate、/customers/merge、/customers/batch-* 也要在
# /customers/{customer_id} 之前注册，否则会被动态路由抢先匹配
app.include_router(customer_tags_router, prefix=settings.api_prefix)
# /contacts/deduplicate、/contacts/{id}/wecom、/contacts/{id}/followups 同样要在
# /contacts/{contact_id} 之前注册，否则会被当成 id 解析
app.include_router(contacts_router, prefix=settings.api_prefix)
app.include_router(customer_router, prefix=settings.api_prefix)
# /products/import-template、/products/export、/skus/import、/skus/export 同样要排在
# /products/{product_id}、/skus/{sku_id} 之前
app.include_router(product_io_router, prefix=settings.api_prefix)
app.include_router(product_router, prefix=settings.api_prefix)
app.include_router(public_pool_router, prefix=settings.api_prefix)
app.include_router(user_router, prefix=settings.api_prefix)
# /leads/import、/leads/export、/leads/import-template 必须在 /leads/{lead_id} 之前
app.include_router(lead_io_router, prefix=settings.api_prefix)
app.include_router(lead_router, prefix=settings.api_prefix)
app.include_router(opportunity_router, prefix=settings.api_prefix)
app.include_router(followup_router, prefix=settings.api_prefix)
app.include_router(inquiry_router, prefix=settings.api_prefix)
app.include_router(task_router, prefix=settings.api_prefix)
app.include_router(timeline_router, prefix=settings.api_prefix)
# /price-rules/import、/costs/import 等静态路径必须在动态路径之前（与产品/客户 io_router 同理）
app.include_router(pricing_io_router, prefix=settings.api_prefix)
app.include_router(pricing_router, prefix=settings.api_prefix)
# 注意顺序：/logistics/rates 等静态路径必须在 /logistics/quotes/{id} 之前注册，
# 否则会被动态路由抢先匹配（这个坑在 /customers/export 上踩过两次）
app.include_router(logistics_router, prefix=settings.api_prefix)
app.include_router(quote_router, prefix=settings.api_prefix)
app.include_router(sample_router, prefix=settings.api_prefix)
app.include_router(approval_router, prefix=settings.api_prefix)
app.include_router(approval_rules_router, prefix=settings.api_prefix)  # 规则接口自带静态/动态顺序，见 rules_router 模块注释
app.include_router(order_router, prefix=settings.api_prefix)
app.include_router(payment_router, prefix=settings.api_prefix)
app.include_router(analytics_router, prefix=settings.api_prefix)
app.include_router(notification_router, prefix=settings.api_prefix)
app.include_router(audit_router, prefix=settings.api_prefix)
app.include_router(file_router, prefix=settings.api_prefix)
app.include_router(settings_router, prefix=settings.api_prefix)
app.include_router(search_router, prefix=settings.api_prefix)
app.include_router(agent_router, prefix=settings.api_prefix)
app.include_router(wecom_router, prefix=settings.api_prefix)
app.include_router(erp_router, prefix=settings.api_prefix)
app.include_router(contract_router, prefix=settings.api_prefix)
app.include_router(cases_router, prefix=settings.api_prefix)


@app.get("/health", tags=["System"])
async def health() -> dict:
    """无鉴权探活端点（监控/负载均衡用）。

    附带数据库连通性检查：进程活着但数据库连不上必须能被监控区分出来，
    否则"后端还在跑但业务全挂"这种状态不会被告警。
    """
    from sqlalchemy import text

    from app.core.database import SessionLocal

    db_ok = True
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    return ok(
        {
            "status": "up" if db_ok else "degraded",
            "app": settings.app_name,
            "database": "ok" if db_ok else "error",
        }
    )
