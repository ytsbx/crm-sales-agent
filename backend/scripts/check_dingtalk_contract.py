"""钉钉发起报文契约回归（P1 修复：`originatorUserId` 必须是钉钉 userid）。

2026-11-25：旧钉钉询价/合同发起通道已删除。本套件保留两段仍有意义的回归：
客户端发报文契约（第 0 节）与「未配置同序 → 422 不外呼」闸门（第 1 节）；
历史钉钉行改为直接落夹具，只回归同步/核定的关闸与不改记录（第 2–3 节）。

跑法（需要 PostgreSQL；**全程不连钉钉、不发任何真实请求**）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_dingtalk_contract.py

## 为什么用假客户端

这条路径唯一"真实"的验证方式是往钉钉发一张审批单，而那会打扰 3 位同事
（发起人 + 两位审批人）。所以这里把客户端的联网层整个换掉：连 access_token
都不去取，`search_user_id_by_name` / `upload_media` / `create_process_instance`
全是桩，只记参数。

## 锁住的三条（对应 P1 修复）

1. 发起时 `originatorUserId` 用的是**钉钉 userid**，不是 CRM 的内部数字 id
   ——原来传 `str(user.id)`，钉钉认不出这个人，审批链第一环就是错的；
2. 模板里的「提报人」控件填的**也是同一个编号**：两处必须同源，
   分开算迟早分叉成两个人；
3. 落库那行 `oa_instances.originator_user_id` 存的是同一个编号
   （排查"这单谁发起的"时只认这一列）。

## 顺带钉死了"字段名写着写着就错了"这条

发起审批实例的请求体里，表单值必须挂在 **`formComponentValues`** 上，**没有 `formValues`**
这个字段（我们原来就写的是 `formValues`）。判据不是猜的：

- 官方 Python SDK `alibabacloud_dingtalk`（2.2.60）里
  `StartProcessInstanceRequest` 只有 `form_component_values`，且标着必填；
  序列化出来就是 `formComponentValues`。SDK 是按 OpenAPI 规格生成的，这等于规格原文；
- 真实历史单的响应里也是同名字段（`formComponentValues`，元素带
  `componentType` / `name` / `bizAlias` / `id` / `value`）。

所以这条断言是"对错断言"，不是"锁现状"：**`formValues` 再出现就该红**。
"""

import asyncio
import sys
import time
import types
from datetime import UTC, datetime

from sqlalchemy import select, text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.database import SessionLocal
from app.core.deps import CurrentUser
from app.modules.user.model import User

FAILURES = []
PREFIX = "CHKOA"

#: 假的钉钉编号——故意与任何 CRM 主键都不同，才能证明"传的是钉钉的、不是 CRM 的"
DING_USER_ID = "DING-USER-CHKOA-001"
DING_QUOTE_ID = "DING-QUOTE-CHKOA-001"
DING_DEPT_ID = 991234567

TEST_CONFIG = {
    "process_code": "PROC-CHKOA",
    "field_map": {
        "inquiry_no": "TextField_CHKOA_NO0",
        "title": "TextField_CHKOA_TITLE0",
    },
    "quote_owner_component": "InnerContactField_CHKOA_QUOTE0",
    "originator_component": "InnerContactField_CHKOA_ORIG0",
    "dept_component": "DepartmentField_CHKOA_DEPT0",
    "image_component": "DDPhotoField_CHKOA_IMG0",
    "inquiry_file_business_type": "inquiry",
}


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=""):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


class FakeClient:
    """假钉钉客户端：只记参数。**不连网、不建任何单、不上传任何文件。**"""

    def __init__(self) -> None:
        self.created = 0
        self.uploaded = 0
        self.searched_name = None
        self.dept_lookup_for = None
        self.last_kwargs: dict = {}

    async def get_process_schema(self, process_code: str) -> dict:
        # 本套件只验证发起报文；补充字段验证另见 test_inquiry_oa_submission。
        return {"schemaContent": {"items": []}}

    async def search_user_id_by_name(self, name: str) -> str | None:
        self.searched_name = name
        return DING_USER_ID

    async def get_user_dept_ids(self, user_id: str) -> list[int]:
        self.dept_lookup_for = user_id
        return [DING_DEPT_ID]

    async def upload_media(self, **_kwargs) -> str:
        self.uploaded += 1
        return "FAKE-MEDIA-1"

    async def create_process_instance(self, **kwargs) -> str:
        self.created += 1
        self.last_kwargs = kwargs
        return f"FAKE-INST-{self.created}"


def fake_request():
    """够 `client_ip()` 用的最小 Request（这条路只读 client.host）。"""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/dingtalk/inquiries/0/oa-approval",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )


async def cleanup(object_key: str | None = None):
    """按前缀清夹具；顺带把落盘的那张测试图删掉。"""
    if object_key:
        from app.modules.file.storage import delete_object

        delete_object(object_key)
    async with SessionLocal() as s:
        await s.execute(text(
            "delete from audit_logs where business_type='oa_instance' and business_id in "
            "(select id from oa_instances where inquiry_id in "
            "(select id from custom_inquiries where inquiry_no like :p))"
        ), {"p": f"{PREFIX}%"})
        await s.execute(
            text(
                "delete from oa_instances where inquiry_id in "
                "(select id from custom_inquiries where inquiry_no like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text(
                "delete from business_files where file_id in "
                "(select id from files where file_name like :p)"
            ),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text("delete from files where file_name like :p"), {"p": f"{PREFIX}%"}
        )
        await s.execute(
            text("delete from custom_inquiries where inquiry_no like :p"),
            {"p": f"{PREFIX}%"},
        )
        await s.execute(
            text("delete from customers where name like :p"), {"p": f"{PREFIX}%"}
        )
        await s.commit()


async def check_client_payload_shape() -> None:
    """客户端那一层：把 httpx 换成桩，断言**发出去的报文长什么样**。

    不联网：`httpx` 是 client 模块里的模块级引用，替换它只影响这个模块
    ——token、发起审批两条请求都在桩里返回，出不了进程。
    """
    from app.modules.dingtalk import client as client_module

    captured: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 200
        content = b"{}"
        text = ""

        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def json(self) -> dict:
            return self._payload

    class _StubAsyncClient:
        def __init__(self, *_a, **_kw) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def post(self, url: str, **kwargs):
            captured.append((url, kwargs))
            if "oauth2/accessToken" in url:
                return _Resp({"accessToken": "STUB-TOKEN", "expireIn": 7200})
            return _Resp({"instanceId": "STUB-INSTANCE-1"})

    real_httpx = client_module.httpx
    client_module.httpx = types.SimpleNamespace(AsyncClient=_StubAsyncClient)
    # 凭证也要给上假的：`_require()` 只判"有没有配"，没配就抛——本地 `.env` 有真凭证
    # 所以本地跑得过，**CI 上直接报 DingTalkNotConfigured**（被 CI 抓到过一次）。
    # 这两行只是让 `_require()` 放行；真正出网的 httpx 已经是上面的桩。
    from app.core.config import settings as app_settings

    saved_key = app_settings.dingtalk_app_key
    saved_secret = app_settings.dingtalk_app_secret
    app_settings.dingtalk_app_key = "STUB-APP-KEY"
    app_settings.dingtalk_app_secret = "STUB-APP-SECRET"
    try:
        client = client_module.DingTalkClient()
        instance_id = await client.create_process_instance(
            process_code=TEST_CONFIG["process_code"],
            form_component_values=[
                {
                    "name": "TextField_CHKOA_NO0",
                    "id": "TextField_CHKOA_NO0",
                    "componentType": "TextField",
                    "value": "CHKOA-1",
                }
            ],
            originator_user_id=DING_USER_ID,
            dept_id=DING_DEPT_ID,
        )
    finally:
        app_settings.dingtalk_app_key = saved_key
        app_settings.dingtalk_app_secret = saved_secret
        client_module.httpx = real_httpx

    create_calls = [
        (url, kwargs) for url, kwargs in captured if "workflow/processInstances" in url
    ]
    check("发起接口被调用次数", len(create_calls), 1)
    check("拿回实例号", instance_id, "STUB-INSTANCE-1")
    if not create_calls:
        return
    _url, kwargs = create_calls[0]
    body = kwargs.get("json") or {}
    check("报文里的发起人是钉钉 userid", body.get("originatorUserId"), DING_USER_ID)
    check("报文里的模板", body.get("processCode"), TEST_CONFIG["process_code"])
    check("报文里的部门", body.get("deptId"), DING_DEPT_ID)
    check_true(
        "表单值挂在 formComponentValues 上（官方 SDK 的字段名）",
        isinstance(body.get("formComponentValues"), list),
        "",
    )
    check_true(
        "报文里没有 formValues 这个野字段",
        "formValues" not in body,
        "曾经写成 formValues——那不是钉钉的字段，表单值会被当成没传",
    )
    first = (body.get("formComponentValues") or [{}])[0]
    check("元素带 id（控件主键）", first.get("id"), "TextField_CHKOA_NO0")
    check("元素带 componentType", first.get("componentType"), "TextField")
    check("元素带 value", first.get("value"), "CHKOA-1")


async def main() -> int:
    from app.core.config import settings
    from app.core.audit import AuditLog
    from app.core.errors import AppError, ErrorCode
    from app.modules.customer.model import Customer
    from app.modules.dingtalk import client as client_module
    from app.modules.dingtalk import router as dt_router
    from app.modules.dingtalk import service as dt
    from app.modules.dingtalk.model import OaInstance
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.file.storage import absolute_path
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.settings.model import SystemSetting

    stamp = int(time.time())
    object_key = f"{PREFIX}/{stamp}.png"

    # 两个 import 点都保存下来：关闸用例把它们换成"一碰就炸"的桩，
    # 证明整段路径没有访问任何审批客户端（旧钉钉发起通道已删除）。
    original_client_getter = client_module.get_client
    original_service_getter = dt.get_client

    # 总闸只在本进程内打开——**客户端是假的，不会有任何真实请求**
    saved_push_off = settings.dingtalk_push_off
    settings.dingtalk_push_off = False

    saved_setting = None
    await cleanup()
    try:
        print("=== 0. 客户端那一层：发出去的报文长什么样 ===")
        await check_client_payload_shape()

        async with SessionLocal() as s:
            admin = (
                await s.execute(select(User).where(User.username == "admin"))
            ).scalars().one()

            # 设置项：库里原来那份先存下来，跑完原样放回（CI 上是新建→删掉）
            row = (
                await s.execute(
                    select(SystemSetting).where(SystemSetting.key == "dingtalk_oa")
                )
            ).scalars().first()
            if row is None:
                s.add(SystemSetting(key="dingtalk_oa", value=TEST_CONFIG))
            else:
                saved_setting = dict(row.value or {})
                row.value = TEST_CONFIG

            customer = Customer(
                name=f"{PREFIX}客户-{stamp}",
                level="A",
                status="active",
                pool_status="private",
                owner_id=admin.id,
            )
            s.add(customer)
            await s.flush()
            customer_id = customer.id

            inquiry = CustomInquiry(
                inquiry_no=f"{PREFIX}{stamp}",
                title="钉钉报文契约回归",
                description="自动化夹具",
                version=1,
                quantity=100,
                target_price=9.9,
                status="open",
                customer_id=customer_id,
                oa_quote_user_id=DING_QUOTE_ID,
                oa_quote_user_name="夹具报价员",
                created_by=admin.id,
            )
            s.add(inquiry)
            await s.flush()
            inquiry_id = inquiry.id

            # 「产品参考图片」是必填的图片控件，所以夹具要真的有一张图落盘。
            # 内容不用是张真图：这条路只按 mime 认类型、按路径读字节，
            # 上传那步是桩，没人会去解它。
            target = absolute_path(object_key)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x89PNG\r\n\x1a\nCHKOA")
            file_row = FileRecord(
                object_key=object_key,
                file_name=f"{PREFIX}参考图-{stamp}.png",
                mime_type="image/png",
                size=target.stat().st_size,
                uploaded_by=admin.id,
            )
            s.add(file_row)
            await s.flush()
            s.add(
                BusinessFile(
                    business_type="inquiry",
                    business_id=inquiry_id,
                    file_id=file_row.id,
                    category="image",
                )
            )
            await s.commit()

        print("=== 1. 关闸 + 未配置同序：明确 422，不外呼、不记单 ===")
        external_attempts = []

        def forbidden_get_client():
            external_attempts.append('get_client')
            raise AssertionError('关闸时不应访问任何钉钉客户端')

        settings.dingtalk_push_off = True
        dt.get_client = forbidden_get_client
        client_module.get_client = forbidden_get_client
        async with SessionLocal() as s:
            admin = (await s.execute(select(User).where(User.username == "admin"))).scalars().one()
            user = CurrentUser(admin, permissions={"quote:manage"}, roles=[], data_scope="all")
            # bdd6efc9 起「总闸关 + 未配置同序 OA」不再记 skipped 草稿单，直接 422
            # 提示保存草稿（与打样「未配置同序的提交一律挡」同一口径）。
            # 从前 skipped → 同轮重试复用同一行那套发起态机在询价入口已不可达
            # （合同发起仍是记 skipped 的老口径，差异见转告清单）。
            for label in ("首次", "重复点击"):
                try:
                    await dt_router.start_inquiry_approval(
                        inquiry_id=inquiry_id, payload=dt_router.StartApproval(),
                        request=fake_request(), user=user, session=s,
                    )
                except AppError as exc:
                    check(f'关闸发起被拒（{label}）', exc.http_status, 422)
                    check_true(f'拒绝理由说清未配置同序（{label}）', '同序' in exc.message, exc.message)
                else:
                    check_true(f'{label} 必须被拒（从前记 skipped）', False)
        async with SessionLocal() as s:
            # ⚠️ inquiry_id 是样品/询价/合同共用列，必须按 oa_type 收窄 ——
            # 否则别的套件残留的 sample 记录 id 撞上本套件的询价 id 就误伤。
            rows = (await s.execute(
                select(OaInstance).where(
                    OaInstance.inquiry_id == inquiry_id, OaInstance.oa_type == 'inquiry'
                )
            )).scalars().all()
        check('被拒后没有落库 OA 记录', len(rows), 0)
        check('关闸没有取得客户端', len(external_attempts), 0)

        print("=== 2. 旧钉钉发起通道已删除：直接构造历史行作为核定夹具 ===")
        # 2026-11-25 删除旧钉钉询价/合同通道后，router 不再向钉钉建单；
        # 历史行只读保留，但同步/核定的关闸行为仍要回归，所以直接落一行夹具。
        async with SessionLocal() as s:
            fixture = OaInstance(
                inquiry_id=inquiry_id,
                inquiry_version=1,
                oa_type="inquiry",
                idempotency_key=f"{PREFIX}-legacy-{stamp}",
                submit_round=1,
                provider="dingtalk",
                status="pending",
                instance_id="FAKE-INST-1",
                originator_user_id=DING_USER_ID,
                created_at=datetime.now(UTC),
            )
            s.add(fixture)
            await s.commit()
            oa_id = fixture.id
        check_true("历史钉钉行已落库", isinstance(oa_id, int) and oa_id > 0, f"id={oa_id}")
        check('旧通道删除后没有外部调用', len(external_attempts), 0)

        print('=== 3. 关闸同步及人工重发：原记录保持不变 ===')
        settings.dingtalk_push_off = True
        dt.get_client = forbidden_get_client
        client_module.get_client = forbidden_get_client
        async with SessionLocal() as s:
            before_sync = (await s.execute(select(OaInstance.__table__).where(OaInstance.id == oa_id))).one()
            # bdd6efc9 删掉了批量手动同步 /dingtalk/oa-sync（手动只剩单条、轮询归定时
            # 任务），单条同步在关闸时直接 422「历史审批仅供查看」——同样不外呼、
            # 不改记录（从前批量入口返回 disabled + 开发阶段未执行的提示）。
            try:
                await dt_router.sync_oa_instance(oa_id=oa_id, user=user, session=s)
            except AppError as exc:
                check('关闸同步被拒', exc.http_status, 422)
                check_true('关闸同步说清历史仅供查看', '仅供查看' in exc.message, exc.message)
            else:
                check_true('关闸同步必须被拒（从前返回 disabled）', False)
            after_sync = (await s.execute(select(OaInstance.__table__).where(OaInstance.id == oa_id))).one()
            check('关闸同步不改审批中的记录', after_sync, before_sync)

            row = await s.get(OaInstance, oa_id)
            row.status = 'needs_review'
            row.error = '夹具：外部结果未知'
            await s.commit()
            before_resend = (await s.execute(select(OaInstance.__table__).where(OaInstance.id == oa_id))).one()
            audit_before = (await s.execute(select(AuditLog.__table__).order_by(AuditLog.id))).all()
            try:
                await dt_router.resolve_oa_instance(
                    oa_id=oa_id, payload=dt_router.ResolveOa(action='resend'),
                    request=fake_request(), user=user, session=s,
                )
            except AppError as exc:
                # bdd6efc9 起关闸重发与关闸同步同一口径：409「历史审批仅供查看，
                # 新审批请使用同序 OA」（从前是 403 FORBIDDEN + 未执行提示）。
                check('关闸重发被拒', exc.http_status, 409)
                check('关闸重发错误码', exc.code, ErrorCode.STATUS_NOT_ALLOWED)
                check_true('关闸重发明确提示仅供查看', '仅供查看' in exc.message, exc.message)
            else:
                check_true('关闸重发必须拒绝', False)
            after_resend = (await s.execute(select(OaInstance.__table__).where(OaInstance.id == oa_id))).one()
            audit_after = (await s.execute(select(AuditLog.__table__).order_by(AuditLog.id))).all()
            check('关闸重发不改结果未知的原记录', after_resend, before_resend)
            # ⚠️ 这里**不能**把两个整表直接传给 `check()`（曾经就是这么写的）。
            # `check()` 在断言**通过时也会 print 出实际值**，而这两侧是整张 audit_logs 表
            # ——实测输出一根 224,732 字节的行（该套件其余 48 行加起来才 10KB）。
            # 本地把它写进文件是毫秒级，CI 把它往回传时卡死：日志停在那一行之前、
            # 之后 136 秒零输出，job 最终被判失败，红了整整两天查不出原因（2026-10-07）。
            # 现在判据**一点没放宽**（仍然要求两侧完全相等），只是不再打印整表。
            audit_same = audit_after == audit_before
            check_true(
                '关闸重发不写成功审计',
                audit_same,
                f'审计 {len(audit_before)} 行 → {len(audit_after)} 行，'
                + ('完全一致' if audit_same else '存在差异（明细见库，不在此展开）'),
            )
        check('发起/同步/重发关闸路径外部调用均为零', len(external_attempts), 0)
    finally:
        # 设置项还回去（CI 上是"新建的那条删掉"）
        async with SessionLocal() as s:
            row = (
                await s.execute(
                    select(SystemSetting).where(SystemSetting.key == "dingtalk_oa")
                )
            ).scalars().first()
            if row is not None:
                if saved_setting is None:
                    await s.delete(row)
                else:
                    row.value = saved_setting
            await s.commit()
        dt.get_client = original_service_getter
        client_module.get_client = original_client_getter
        settings.dingtalk_push_off = saved_push_off
        await cleanup(object_key)

    print()
    if FAILURES:
        print("FAILED " + str(len(FAILURES)) + " 项：" + "、".join(FAILURES))
        return 1
    print("钉钉发起报文契约回归 全部通过（全程未连钉钉）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
