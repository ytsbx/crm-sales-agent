"""客户批量导入导出。

为什么用 CSV 而不是 xlsx：Excel 能直接打开和另存 CSV，中文用 utf-8-sig 编码不会乱码，
而且不用额外装 openpyxl 之类的依赖——对"把历史名单搬进来"这件事，CSV 完全够用。

导入的三条规矩：
1. 每行都查重，疑似重复的**跳过并报告**，不往库里塞脏数据；
2. 一行出错不影响其它行，最后汇总"成功 N / 跳过 M / 失败 K"；
3. 负责人按登录名匹配，匹配不上就用当前操作人，避免导入的数据没人管。
"""

import csv
import io

from fastapi import UploadFile

from app.core.errors import AppError, ErrorCode
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


def csv_bytes(rows: list[list], headers: list[str]) -> bytes:
    """生成带 BOM 的 CSV：Excel 双击打开不乱码。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


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
    """把上传的 CSV 解析成字典列表。中文 Excel 常存成 GBK，这里两种编码都试。"""
    raw = await file.read()
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise AppError(ErrorCode.PARAM_ERROR, "文件编码无法识别，请另存为 UTF-8 或 GBK 的 CSV")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise AppError(ErrorCode.PARAM_ERROR, "文件是空的，或者没有表头")
    missing = [name for name in ("客户名称",) if name not in reader.fieldnames]
    if missing:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"表头缺少：{'、'.join(missing)}。请先下载导入模板按格式填写",
        )
    return [row for row in reader if (row.get("客户名称") or "").strip()]


async def resolve_owner(session, username: str | None, default_user_id: int) -> int:
    """负责人按登录名匹配，匹配不上就用当前操作人。"""
    if not username or not username.strip():
        return default_user_id
    from sqlalchemy import select

    row = (
        await session.execute(select(User).where(User.username == username.strip()))
    ).scalar_one_or_none()
    return row.id if row else default_user_id
