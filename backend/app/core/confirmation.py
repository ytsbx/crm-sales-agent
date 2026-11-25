"""需要**明确确认**才能执行的动作，共用一份判据。

为什么要单独一个模块（审查 2026-10-10 的清单）：

有些按钮点一下就真的执行了，而它们会**切换当前依据、覆盖金额、结束流程、
登记商务事实、或者写进外部系统**。其中「新建版本」最典型 —— 它不只是"多一份草稿"：

    复制当前最新版 → 创建下一版 → **切换当前版本** → **把报价状态改回草稿**
    → **自动结束旧版还在走的审批** → 旧版随后不能继续登记发送/客户接受

而系统里**没有**"撤销新版本、恢复上述状态"的入口。所以它必须先弹一次确认。

判据只有这一份：`require_confirmation()`。前端弹窗里用户点过"确认"之后，
请求带上 `confirm=True`；不带就拒。

⚠️ **报错文案要能直接展示给用户**：前端把它当作确认弹窗的正文
（"点了会怎样"），所以它必须说清后果，而不是一句"参数错误"。
"""

from __future__ import annotations

from app.core.errors import AppError, ErrorCode


def confirmation_message(*, action: str, detail: str = "") -> str:
    """组装那句给用户看的说明（确认弹窗的正文与报错文案共用一份）。"""
    message = f"「{action}」不是普通操作，请确认后继续"
    if detail:
        message += f"：{detail}"
    return message


def require_confirmation(confirm: bool, *, action: str, detail: str = "") -> None:
    """没带 `confirm=True` 就拒绝，并把"这个动作会做什么"讲清楚。

    `action` 用**业务语言**写这个动作的名字（"新建报价版本"而不是 `create_version`），
    因为它会出现在给用户看的文案里。
    """
    if confirm:
        return
    raise AppError(
        ErrorCode.CONFIRM_REQUIRED,
        confirmation_message(action=action, detail=detail),
        422,
    )
