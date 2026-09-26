"""客户批量导入导出。

为什么用 CSV 而不是 xlsx：Excel 能直接打开和另存 CSV，中文用 utf-8-sig 编码不会乱码，
而且不用额外装 openpyxl 之类的依赖——对"把历史名单搬进来"这件事，CSV 完全够用。

导入的三条规矩：
1. 每行都查重，疑似重复的**跳过并报告**，不往库里塞脏数据；
2. 一行出错不影响其它行，最后汇总"成功 N / 跳过 M / 失败 K"；
3. 负责人按登录名匹配，匹配不上就用当前操作人，避免导入的数据没人管。

CSV 编解码的公共部分在 `app/core/csvio.py`（客户/线索/产品共用）。
"""

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
