"""客户批量导入导出。

为什么用 CSV 而不是 xlsx：Excel 能直接打开和另存 CSV，中文用 utf-8-sig 编码不会乱码，
而且不用额外装 openpyxl 之类的依赖——对"把历史名单搬进来"这件事，CSV 完全够用。

导入的三条规矩：
1. 每行都查重，疑似重复的**跳过并报告**，不往库里塞脏数据；
2. 一行出错不影响其它行，最后汇总"成功 N / 跳过 M / 失败 K"；
3. 负责人按登录名匹配：**只在文件里没填负责人时**才落到导入人；
   登录名写错或账号已停用是**这一行失败**（第七批 7.5）——
   原来"匹配不上就用当前操作人"会把别人的客户悄悄改派，且毫无痕迹。

CSV 编解码的公共部分在 `app/core/csvio.py`（客户/线索/产品共用）。
"""

from datetime import UTC, datetime

from fastapi import UploadFile
from sqlalchemy import select

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

    解析不了、或是未来日期一律返回 None。需要"为什么是 None"时用
    `parse_date_checked`：导入接口要逐行告诉用户填错在哪。
    """
    return parse_date_checked(value)[0]


def parse_date_checked(value: str | None) -> tuple[datetime | None, str | None]:
    """解析日期列，返回 (值, 错误原因)。

    为什么要带原因（第七批 7.5）：原来解析不了和未来日期都静默返回 None，
    等于把"用户填错"变成"这行没填"。历史联系时间一丢，冷落/回收判断就全错，
    而且没有任何地方会提示 —— 现在这两种情况都明确报给用户，且**只有真的
    没填**（空白）才算"未知"。
    """
    text = (value or "").strip()
    if not text:
        return None, None
    normalized = text.strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        if parsed > datetime.now(UTC):
            return None, f"最后联系日期不能是未来日期（收到 {text}）"
        return parsed, None
    return None, f"最后联系日期格式应为 YYYY-MM-DD（收到 {text}）"


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
    return (await resolve_owner_checked(session, username, default_user_id))[0]


async def resolve_owner_checked(
    session, username: str | None, default_user_id: int
) -> tuple[int, str | None, dict]:
    """负责人映射：返回 (owner_id, 错误原因, 映射说明)。

    第七批 7.5 的口径（原实现是"匹配不上就落到导入人名下"）：
    - 空白 → 用导入人，并在映射说明里写清"默认：导入人"，预览时能核对；
    - 登录名不存在 → **这一行失败**，不把客户偷偷占到导入人名下。
      历史名单里写错的负责人一旦静默变成导入人，等于把别人的客户改派了，
      而且没有任何痕迹；
    - 账号已停用 → 同样这一行失败：停用的人不该再接新客户（原来只看账号存在）。
    """
    login = (username or "").strip()
    if not login:
        row = await session.get(User, default_user_id)
        return (
            default_user_id,
            None,
            {
                "login": None,
                "resolved": "import_operator",
                "owner_id": default_user_id,
                "owner_name": getattr(row, "name", None),
                "note": "文件未填负责人，按导入人归属",
            },
        )
    found = (
        await session.execute(select(User).where(User.username == login))
    ).scalar_one_or_none()
    if found is None:
        return (
            default_user_id,
            f"找不到登录名为「{login}」的账号，请核对负责人登录名（不会自动归到导入人名下）",
            {},
        )
    if found.status != "active":
        return (
            default_user_id,
            f"账号「{login}」（{found.name}）已停用，不能作为客户负责人",
            {},
        )
    return (
        found.id,
        None,
        {
            "login": login,
            "resolved": "username",
            "owner_id": found.id,
            "owner_name": found.name,
            "note": None,
        },
    )
