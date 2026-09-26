"""线索批量导入导出。

与客户导入同一套规矩（见 customer/io.py 的说明），区别在字段与查重口径：
线索查重用"公司名 + 手机号"，并且**只跳过完全同号的**，不做模糊打分 ——
线索本来就是粗筛出来的名单，用客户的相似度阈值会把大量真实新线索挡在门外。
"""

from app.modules.lead.service import STATUS_LABEL

TEMPLATE_HEADERS = [
    "线索名称",
    "公司名称",
    "联系人",
    "手机号",
    "邮箱",
    "省份",
    "来源",
    "负责人登录名",
    "备注",
]

EXPORT_HEADERS = [
    "线索名称",
    "公司名称",
    "联系人",
    "手机号",
    "邮箱",
    "省份",
    "来源",
    "状态",
    "负责人",
    "最近跟进",
    "创建时间",
]


def lead_export_row(lead, owner_name: str | None) -> list:
    return [
        lead.name,
        lead.company_name or "",
        lead.contact_name or "",
        lead.mobile or "",
        lead.email or "",
        lead.region or "",
        lead.source or "",
        STATUS_LABEL.get(lead.status, lead.status),
        owner_name or "",
        lead.last_followup_at.strftime("%Y-%m-%d") if lead.last_followup_at else "",
        lead.created_at.strftime("%Y-%m-%d") if lead.created_at else "",
    ]
