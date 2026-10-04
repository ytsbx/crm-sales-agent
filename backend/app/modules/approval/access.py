"""审批列表与操作共用的当前节点资格判断。数据范围由调用方先校验。"""

from app.core.deps import CurrentUser
from app.modules.approval.model import ApprovalInstance


def approval_denial(
    user: CurrentUser, instance: ApprovalInstance, *, price_can_approve: bool,
    check_assignee: bool = True,
) -> str | None:
    """返回拒绝原因；None 表示当前用户符合该节点的既有审批规则。

    仅检查拟转交接收人时跳过现有处理人限制，其余节点规则全部保留。
    """
    summary = instance.summary or {}
    co_sign = summary.get("co_sign")
    admin = "admin" in user.roles
    if instance.current_node == "co_sign" and co_sign:
        allowed = list(co_sign.get("role_codes") or [])
        if not admin and not (set(user.roles) & set(allowed)):
            return f"该审批处于「{co_sign.get('label', '会签')}」节点，需要 {'、'.join(allowed)} 处理"
        if instance.applicant_id == user.id and not admin:
            return "不能审批自己提交的报价"
        return None

    if not admin and (not user.has("quote:approve") or not price_can_approve):
        return "你的角色没有审批低价报价的权限"
    allowed_roles = summary.get("node_role_codes") or []
    if allowed_roles and not admin and not (set(user.roles) & set(allowed_roles)):
        return f"该审批需要「{summary.get('node_label', '上级')}」处理，你的角色无权批准"
    assignee_id = summary.get("current_assignee_id")
    if check_assignee and assignee_id and not admin:
        try:
            assigned_to_me = int(assignee_id) == user.id
        except (TypeError, ValueError):
            return "当前审批处理人配置无效，请联系管理员"
        if not assigned_to_me:
            return f"该审批已转交给「{summary.get('current_assignee_name', '他人')}」处理"
    if instance.applicant_id == user.id and not admin:
        return "不能审批自己提交的报价"
    return None
