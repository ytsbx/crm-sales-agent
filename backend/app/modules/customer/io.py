"""客户批量导入导出。

为什么用 CSV 而不是 xlsx：Excel 能直接打开和另存 CSV，中文用 utf-8-sig 编码不会乱码，
而且不用额外装 openpyxl 之类的依赖——对"把历史名单搬进来"这件事，CSV 完全够用。

导入的三条规矩：
1. 每行都查重，疑似重复的**跳过并报告**，不往库里塞脏数据；
2. 一行出错不影响其它行，最后汇总"成功 N / 跳过 M / 失败 K"；
3. 负责人按登录名匹配，匹配不上就用当前操作人，避免导入的数据没人管。

CSV 编解码的公共部分在 `app/core/csvio.py`（客户/线索/产品共用）。
"""

from datetime import UTC, datetime

from fastapi import UploadFile

from app.core.csvio import parse_csv_upload
from app.modules.customer.model import Customer
from app.modules.user.model import User

TEMPLATE_HEADERS = [
    "客户名称",
    "客户简称",
    "客户等级",
    "省份",
    "详细地址",
    "客户来源",
    "负责人登录名",
    # 老数据迁移（§六 :167「导入不应重置客户最近有效联系时间」）：
    # 历史名单里通常有这个日期，填了就按真实的写；不填才落回"刚建档"。
    # 没有它，500 个三年没联系的老客户进系统当天全被当成"今天刚联系过"，
    # 冷落提醒要等一整个周期才生效——上线第一个月等于没有预警。
    "最后联系日期",
    "备注",
]

EXPORT_HEADERS = [
    "客户名称",
    "客户简称",
    "等级",
    "省份",
    "详细地址",
    "来源",
    "负责人",
    "状态",
    "公海/私海",
    "最近跟进",
    "创建时间",
]


def parse_date(value: str | None) -> datetime | None:
    """解析导入文件里的日期列（YYYY-MM-DD / YYYY/M/D / YYYY.M.D）。

    两种取值明确返回 None 而不是猜：
    - 解析不了的字符串（不把垃圾数据编成今天，那才是真的"重置联系时间"）；
    - **未来日期**（老名单里出现的未来日期多半是填错，不能让它变成"永不冷落"）。
    """
    if not value:
        return None
    text = value.strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed if parsed <= datetime.now(UTC) else None
    return None


def customer_export_row(customer: Customer, owner_name: str | None) -> list:
    return [
        customer.name,
        customer.short_name or "",
        customer.level or "",
        customer.region or "",
        customer.address or "",
        customer.source or "",
        owner_name or "",
        customer.status,
        "公海" if customer.pool_status == "public" else "私海",
        customer.last_followup_at.strftime("%Y-%m-%d") if customer.last_followup_at else "",
        customer.created_at.strftime("%Y-%m-%d") if customer.created_at else "",
    ]


async def parse_upload(file: UploadFile) -> list[dict]:
    """把上传的 CSV 解析成字典列表（客户模板口径）。"""
    return await parse_csv_upload(file, required_headers=["客户名称"], label="文件")


async def resolve_owner(session, username: str | None, default_user_id: int) -> int:
    """负责人按登录名匹配，匹配不上就用当前操作人。"""
    if not username or not username.strip():
        return default_user_id
    from sqlalchemy import select

    row = (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()
    return row.id if row else default_user_id
