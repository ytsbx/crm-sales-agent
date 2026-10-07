"""批量导入的公共骨架：逐行 SAVEPOINT、按唯一行计数的结果、预览快照。

## 为什么要有这个文件

第七批 7.1 / 7.2 / 7.6 是同一类问题的三个面，四个导入路由（客户/线索/产品/价格）
各写一份，于是就各漏一份：

1. **一行失败污染整个 session**（7.1）
   原来的写法是 `try: session.add(...); await session.flush() except Exception: 记一行失败`
   —— 捕获了异常却**没有回滚这一行的事务**。PostgreSQL 一个语句报错后事务就进入
   失败态，后面每一行都会拿着 `PendingRollbackError` 继续失败：好行、坏行、好行
   的最后结果是"只剩第一行成功"。正确做法是每行一个 SAVEPOINT，行内失败整体
   回滚到该行之前，session 仍然可用。

2. **同一行多个问题被算成多行失败**（7.6）
   `failed.append(...)` 一人一条，统计口径就变成"错误条数"而不是"失败行数"。
   这里用 `ImportReport.failed_row` 按行号合并，`failed_count` 是**唯一行号数**。

3. **预览和执行是两次请求**（7.6）
   中间别人可能改了数据，用户自己也可能换了文件。底线是执行时**重新校验**
   （各路由都做到了），但更要紧的是：**结论一旦和预览不一样就不能提交**。
   为此预览返回一个 `preview_token`：文件摘要 + 每行计划的压缩快照 + HMAC 签名。
   它是自包含的，不落库、不需要新表；执行时带上它，`finalize` 会比对
   「文件摘要 + 每行结论」，**不一致就整体回滚并报 40902，要求重新预览**。
   （原实现算完差异只塞进响应体、然后照常 commit —— 等于"先改数据再提醒"，
   用户确认的是预览里那份结果，落库的却是另一份，等于没有防护。）
   签名用 `settings.jwt_secret`，与登录令牌同一把密钥；token 只用于比对，
   不携带任何权限语义，也不影响"两次请求之间权限被收回"的判定
   （数据范围在执行时照常重新校验）。

4. **完整错误清单**
   两道闸一起才成立：① 单个文件的数据行数不超过 `settings.import_max_rows`
   （超了当场拒绝、让用户拆文件）；② `failed` 超过 `settings.import_max_error_rows`
   才截断。因为失败行数**永远 ≤ 总行数**，①的阈值不高于②时截断**不会发生** ——
   用户拿到的就是完整清单，不需要再给一个"补下载剩下的错误"的入口。
   万一配置被人改坏真的截断了，响应里仍会带 `failed_truncated=true`：
   **截断必须说出来**，不能让人以为拿到的是全部。
"""

import base64
import hashlib
import hmac
import json
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppError, ErrorCode

#: 行结果分类。预览快照与正式执行用同一套码，才能逐行比对"结论变没变"。
OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"
OUTCOME_DISPUTED = "disputed"


class RowRejected(Exception):
    """纯业务拒绝（与现有资料冲突、范围外…）：这一行不算数，但**不是数据库错误**。

    为什么单独一个异常类型：`row_savepoint` 的异常通道是给数据库错误用的，
    业务拒绝走同一条路会多做一次无谓的回滚，错误文案也容易被
    "写入失败：..." 这种前缀盖住，用户看不出到底哪里要改。
    """


class RowSkipped(Exception):
    """按业务规则跳过（重名、编码已存在…）：不是错误，进 skipped 统计。"""


@asynccontextmanager
async def row_savepoint(session: AsyncSession):
    """一行的 SAVEPOINT 边界。**异常必须在 with 外面接住**。

    用法：
        try:
            async with row_savepoint(session):
                session.add(obj)
                await session.flush()
        except Exception as exc:
            report.failed_row(index, name, str(exc))

    退出 with 时提交 savepoint；抛异常则回滚到 savepoint 并把这一行里
    `add` 过但还没 flush 的对象从 session 摘掉——不摘的话它们会留在 pending，
    等最后一次 `commit()` 时被一起写进去，等于"报错的那一行偷偷成功了"。
    """
    nested = await session.begin_nested()
    try:
        yield nested
        await nested.commit()
    except Exception:
        await nested.rollback()
        for obj in list(session.new):
            session.expunge(obj)
        raise


class RowErrors:
    """一行内的字段问题收集器。

    为什么不是"解析函数直接抛异常"：一行里往往同时错好几处（数量下限大于上限 +
    生效期倒置 + 币种不支持），逐条抛只能看到第一条，用户改一次传一次。
    收集起来一次性告诉他，才是"逐行错误反馈"该有的样子。
    """

    def __init__(self, row: int, name: str = "") -> None:
        self.row = row
        self.name = name
        self.reasons: list[str] = []

    def add(self, reason: str) -> None:
        self.reasons.append(reason)

    def __bool__(self) -> bool:
        return bool(self.reasons)

    def __len__(self) -> int:
        return len(self.reasons)

    # ------------------------------------------------------------ 数值

    def decimal(
        self,
        raw: str | None,
        field: str,
        *,
        required: bool = False,
        non_negative: bool = False,
        positive: bool = False,
        minimum: Decimal | None = None,
        maximum: Decimal | None = None,
        integral: bool = False,
    ) -> Decimal | None:
        """解析十进制字段并做合法性检查；不合法时记原因并返回 None。

        NaN / Infinity 必须挡在这里：`Decimal("NaN")` 是**合法**的 Decimal，
        放进库会变成 NULL 或报错，放进金额比较会一路返回 False ——
        最后表现成"这行算出来的毛利不对"，而不是"这行数据不合法"。
        """
        text = (raw or "").strip()
        if not text:
            if required:
                self.add(f"{field}必填")
            return None
        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError):
            self.add(f"{field}不是有效数字（收到 {text!r}）")
            return None
        if not value.is_finite():
            self.add(f"{field}不是有限数字（收到 {text!r}）")
            return None
        if integral and value != value.to_integral_value():
            self.add(f"{field}必须是整数（收到 {text!r}）")
            return None
        if non_negative and value < 0:
            self.add(f"{field}不能为负数（收到 {text!r}）")
            return None
        if positive and value <= 0:
            self.add(f"{field}必须大于 0（收到 {text!r}）")
            return None
        if minimum is not None and value < minimum:
            self.add(f"{field}不能小于 {minimum}（收到 {text!r}）")
            return None
        if maximum is not None and value > maximum:
            self.add(f"{field}不能大于 {maximum}（收到 {text!r}）")
            return None
        return value

    def integer(
        self,
        raw: str | None,
        field: str,
        *,
        required: bool = False,
        non_negative: bool = False,
    ) -> int | None:
        """整数列。**不做 `int(float(x))` 截断**：MOQ 2.9 截成 2 是静默改数。"""
        value = self.decimal(
            raw, field, required=required, non_negative=non_negative, integral=True
        )
        return None if value is None else int(value)

    # ------------------------------------------------------------ 日期

    def date_value(
        self,
        raw: str | None,
        field: str,
        *,
        required: bool = False,
        allow_future: bool = True,
        not_before: date | None = None,
    ) -> date | None:
        """解析 YYYY-MM-DD（容忍 / 与 . 分隔）。解析不了就报错，**不静默变空**。

        原来这里错了就返回 None，等于把"用户填错"变成"这行没填"——
        价格有效期、历史联系时间这类字段一旦静默丢失，后面所有的
        "按生效期取价"、"多久没联系"判断都会得出一个看似正常的错结论。
        """
        text = (raw or "").strip()
        if not text:
            if required:
                self.add(f"{field}必填")
            return None
        normalized = text.replace("/", "-").replace(".", "-")
        parsed: date | None = None
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(normalized, fmt).replace(tzinfo=UTC).date()
                break
            except ValueError:
                continue
        if parsed is None:
            self.add(f"{field}格式应为 YYYY-MM-DD（收到 {text!r}）")
            return None
        if not allow_future and parsed > datetime.now(UTC).date():
            self.add(f"{field}不能是未来日期（收到 {text}）")
            return None
        if not_before is not None and parsed < not_before:
            self.add(f"{field}不能早于 {not_before.isoformat()}（收到 {text}）")
            return None
        return parsed

    def text_value(
        self, raw: str | None, field: str, *, max_length: int
    ) -> str | None:
        """文本列。超长必须报错：数据库会直接抛 `value too long for type`，
        那行错误就变成一句数据库方言的错误码，用户看不懂也不知道该改哪里。
        空白一律归一成 None（"没填"和"填了空字符串"在业务上没有区别）。"""
        text = (raw or "").strip()
        if not text:
            return None
        if len(text) > max_length:
            self.add(f"{field}长度不能超过 {max_length}（当前 {len(text)}）")
            return None
        return text


class ImportReport:
    """一次导入（或一次预览）的结果。

    `failed_count` / `skipped_count` / `created_count` 一律按**行**计：
    同一行的三个问题是一条失败，不是三条。
    """

    def __init__(self, module: str, rows_n: int, *, failed_label: str = "失败") -> None:
        # §7.6：先卡文件规模。失败行数**永远 ≤ 总行数**，所以
        # "文件行数上限 ≤ 失败清单上限"就保证 `failed` 不会被截断 ——
        # 用户拿到的必然是完整清单，不需要另开一个补下载的口子。
        # 卡在这里而不是各路由里，是为了**不可能漏**：导入都要经过这个构造。
        limit = settings.import_max_rows
        if limit > 0 and rows_n > limit:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"文件有 {rows_n} 行，超过单次导入上限 {limit} 行："
                "请拆成多个文件分批导入（这样失败清单也能一次看全）。",
                422,
            )
        self.module = module
        self.rows_n = rows_n
        self.failed_label = failed_label
        self.created: list[dict] = []
        self.skipped: list[dict] = []
        self.disputed: list[dict] = []
        self.updated: list[dict] = []
        #: 行号 -> {"row", "name", "reason", "problems"}：按行去重的失败清单
        self._failed: dict[int, dict] = {}

    # ------------------------------------------------------------ 记录

    def created_row(self, row: int, name: str = "", **extra) -> dict:
        item = {"row": row, "name": name, **extra}
        self.created.append(item)
        return item

    def updated_row(self, row: int, name: str = "", **extra) -> dict:
        """同一条记录被本次导入更新（例如同生效日的成本版本）。"""
        item = {"row": row, "name": name, **extra}
        self.updated.append(item)
        return item

    def skipped_row(self, row: int, name: str, reason: str) -> dict:
        item = {"row": row, "name": name, "reason": reason}
        self.skipped.append(item)
        return item

    def disputed_row(self, row: int, name: str, **extra) -> dict:
        item = {"row": row, "name": name, **extra}
        self.disputed.append(item)
        return item

    def failed_row(self, row: int, name: str, reason: str | list[str]) -> dict:
        """记一行失败。同一行再次调用是**追加原因**，不会变成第二条失败。"""
        problems = [reason] if isinstance(reason, str) else [r for r in reason if r]
        problems = [p for p in problems if p]
        existing = self._failed.get(row)
        if existing is None:
            existing = {"row": row, "name": name, "reason": "", "problems": []}
            self._failed[row] = existing
        if name and not existing.get("name"):
            existing["name"] = name
        for problem in problems:
            if problem not in existing["problems"]:
                existing["problems"].append(problem)
        existing["reason"] = "；".join(existing["problems"])
        return existing

    # ------------------------------------------------------------ 统计

    @property
    def failed(self) -> list[dict]:
        return [self._failed[row] for row in sorted(self._failed)]

    @property
    def failed_count(self) -> int:
        return len(self._failed)

    @property
    def created_count(self) -> int:
        return len(self.created)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)

    @property
    def updated_count(self) -> int:
        return len(self.updated)

    def failure_rows(self) -> set[int]:
        return set(self._failed)

    # ------------------------------------------------------------ 预览快照

    def plan(self) -> dict[str, str]:
        """行号 -> 结论码。用于预览与执行之间比对"结论变没变"。"""
        plan: dict[str, str] = {}
        for item in self.created:
            plan[str(item["row"])] = OUTCOME_CREATED
        for item in self.updated:
            plan[str(item["row"])] = OUTCOME_UPDATED
        for item in self.skipped:
            plan[str(item["row"])] = OUTCOME_SKIPPED
        for item in self.disputed:
            plan[str(item["row"])] = OUTCOME_DISPUTED
        for row in self._failed:
            plan[str(row)] = OUTCOME_FAILED
        return plan

    # ------------------------------------------------------------ 响应

    def payload(self, *, message: str, preview: bool = False, extra: dict | None = None) -> dict:
        """统一的响应体。

        `failed` 默认全量；超过 `settings.import_max_error_rows` 才截断，
        并在 `failed_truncated` 里说明——截断必须可见。
        """
        failed = self.failed
        limit = settings.import_max_error_rows
        truncated = limit > 0 and len(failed) > limit
        body = {
            "total": self.rows_n,
            "created_count": self.created_count,
            "updated_count": self.updated_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "created": self.created,
            "skipped": self.skipped,
            "updated": self.updated,
            "failed": failed[:limit] if truncated else failed,
            "failed_truncated": truncated,
            "failed_total": len(failed),
            "preview": preview,
        }
        if self.disputed:
            body["disputed_count"] = len(self.disputed)
            body["disputed"] = self.disputed
        if extra:
            body.update(extra)
        return ok_body(body, message)


def ok_body(body: dict, message: str) -> dict:
    """与 `app.core.response.ok` 同一形状；单独放这里避免 core 内部循环引用。"""
    return {"code": 0, "message": message, "data": body}


# ==================================================================== 预览快照


def _sign(payload: bytes) -> str:
    return hmac.new(
        settings.jwt_secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def make_preview_token(module: str, file_sha256: str, plan: dict[str, str]) -> str:
    """预览快照：文件摘要 + 每行结论，压缩后签名。

    只放进 token 不进库，是为了让 7.6 的"预览→确认"闭环不需要新表和新迁移；
    代价是 token 会随行数变大（1000 行约几 KB），作为表单字段完全够用。
    """
    raw = json.dumps(
        {"m": module, "f": file_sha256, "p": plan},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    compressed = zlib.compress(raw, 9)
    return f"{base64.urlsafe_b64encode(compressed).decode()}.{_sign(compressed)}"


def read_preview_token(token: str, module: str) -> tuple[str, dict[str, str]]:
    """校验并解开预览快照，返回 (文件摘要, 行结论)。

    签名不对、模块不对、结构不对一律拒绝：这份快照会直接影响
    "要不要提示用户结论变了"，让它可以被伪造等于让提示失灵。
    """
    try:
        encoded, signature = token.split(".", 1)
        compressed = base64.urlsafe_b64decode(encoded.encode())
    except Exception as exc:  # noqa: BLE001 —— 任何结构问题都是"这份快照不可用"
        raise AppError(ErrorCode.PARAM_ERROR, "预览快照无法识别，请重新预览", 422) from exc
    if not hmac.compare_digest(_sign(compressed), signature):
        raise AppError(ErrorCode.PARAM_ERROR, "预览快照已失效，请重新预览", 422)
    try:
        data = json.loads(zlib.decompress(compressed).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.PARAM_ERROR, "预览快照无法解析，请重新预览", 422) from exc
    if data.get("m") != module:
        raise AppError(ErrorCode.PARAM_ERROR, "预览快照与当前导入类型不一致，请重新预览", 422)
    plan = data.get("p")
    if not isinstance(plan, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "预览快照无法解析，请重新预览", 422)
    return str(data.get("f") or ""), {str(k): str(v) for k, v in plan.items()}


#: 结论码的人话。提示里说"第 7 行：预览时说要新增，现在失败"，比给个数字有用。
OUTCOME_LABEL = {
    OUTCOME_CREATED: "将新增",
    OUTCOME_UPDATED: "将更新",
    OUTCOME_SKIPPED: "将跳过",
    OUTCOME_DISPUTED: "进待裁定",
    OUTCOME_FAILED: "失败",
}


def diff_preview(
    planned: dict[str, str], actual: dict[str, str], *, file_sha256: str, planned_file: str
) -> dict:
    """比对预览结论与本次执行结论。

    返回的 `changed` 只列**结论变了**的行（含预览时不在本次结果里的行），
    每项都带上下两段人话，前端可以直接显示"哪几行和预览不一样了"。
    """
    changed: list[dict] = []
    for row in sorted(set(planned) | set(actual), key=lambda x: int(x)):
        before = planned.get(row)
        after = actual.get(row)
        if before == after:
            continue
        changed.append(
            {
                "row": int(row),
                "before": before,
                "before_label": OUTCOME_LABEL.get(before or "", "未出现在预览里"),
                "after": after,
                "after_label": OUTCOME_LABEL.get(after or "", "本次未涉及"),
            }
        )
    return {
        "file_changed": bool(planned_file) and planned_file != file_sha256,
        "planned_file_sha256": planned_file,
        "file_sha256": file_sha256,
        "changed_rows": changed,
    }


async def finalize(
    session: AsyncSession,
    report: ImportReport,
    *,
    module: str,
    operator_id: int,
    file_name: str | None,
    file_sha256: str,
    ip: str | None,
    preview: bool,
    preview_token: str | None = None,
    business_type: str | None = None,
    extra: dict | None = None,
) -> dict:
    """收尾：预览就整体回滚，正式执行则写审计并提交。

    这里刻意把「提交失败」抛成异常而不是返回成功统计：原来的实现
    `await session.commit()` 失败时异常会冒到 FastAPI 变成 500，但**响应体里
    已经拼好的成功计数**在某些调用方那里仍会被用到（例如脚本只看 data）。
    现在明确：**提交没成功就没有成功统计**。

    `extra`：各模块自己附加的响应字段（例如客户导入的负责人映射表），
    预览与正式执行都会带上 —— 这两步的返回结构一致，前端才不用写两套。
    """
    body_extra: dict = dict(extra or {})
    if preview:
        # 预览：完整跑一遍校验与冲突检查后整体回滚，不落任何数据
        await session.rollback()
        body_extra["preview_token"] = make_preview_token(
            module, file_sha256, report.plan()
        )
        message = (
            f"预览完成（未写入）：将新增 {report.created_count} 条，"
            f"更新 {report.updated_count} 条，跳过 {report.skipped_count} 条，"
            f"失败 {report.failed_count} 条"
        )
        if report.disputed:
            message += f"，其中 {len(report.disputed)} 条疑似撞单会进待裁定"
        return report.payload(message=message, preview=True, extra=body_extra)

    if preview_token:
        planned_file, planned = read_preview_token(preview_token, module)
        preview_diff = diff_preview(
            planned, report.plan(), file_sha256=file_sha256, planned_file=planned_file
        )
        # §7.6 的核心：预览之后**文件本身或每行结论**变了，就不能再提交。
        # 原来的实现算完差异只把它塞进响应体、**然后继续往下 commit** ——
        # 等于"先改数据、再提醒用户"，而他确认时看到的是另一份结果。
        # 这里的纪律与并发冲突一致：**冲突就整体回滚，要求重新预览**，
        # 不是"提交完再告诉他不一致"（那时数据已经改了，提示只是事后通知）。
        if preview_diff["file_changed"] or preview_diff["changed_rows"]:
            await session.rollback()
            changed = preview_diff["changed_rows"]
            detail = "、".join(
                f"第 {item['row']} 行（预览时{item['before_label']}，本次{item['after_label']}）"
                for item in changed[:5]
            )
            if len(changed) > 5:
                detail += f"，等共 {len(changed)} 行"
            reason = (
                "导入文件与预览时不是同一份（内容已变化）"
                if preview_diff["file_changed"]
                else "导入结论与预览时不一致"
            )
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                f"{reason}：{detail or '（结论明细见重新预览结果）'}。"
                "本次没有写入任何数据，请重新预览并确认。",
                409,
            )
        body_extra["preview_diff"] = preview_diff

    from app.core.audit import write_audit  # 局部导入：避免 core 内部循环引用

    await write_audit(
        session,
        operator_id=operator_id,
        action="import",
        business_type=business_type or module,
        after={
            "file": file_name,
            "file_sha256": file_sha256,
            "created": report.created_count,
            "updated": report.updated_count,
            "skipped": report.skipped_count,
            "failed": report.failed_count,
            "failed_rows": sorted(report.failure_rows()),
        },
        ip=ip,
    )
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 —— 提交失败不能返回成功统计
        await session.rollback()
        raise AppError(
            ErrorCode.SYSTEM_ERROR,
            f"导入提交失败，本次没有任何数据写入（{str(exc)[:120]}），请重试或联系管理员",
            500,
        ) from exc

    message = (
        f"导入完成：成功新增 {report.created_count} 条，更新 {report.updated_count} 条，"
        f"跳过 {report.skipped_count} 条，失败 {report.failed_count} 条"
        f"（共 {report.rows_n} 行）"
    )
    if report.disputed:
        message += f"；其中 {len(report.disputed)} 条疑似撞单已进待裁定"
    if body_extra.get("preview_diff", {}).get("changed_rows"):
        message += "；注意：有行的结论与预览时不一致，详见 preview_diff"
    return report.payload(message=message, extra=body_extra)
