"""钉钉发起报文契约回归（P1 修复：`originatorUserId` 必须是钉钉 userid）。

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

## 诚实标注：一条**没验证**的

`client.create_process_instance` 的请求体里，表单值挂在 `formValues` 这个键上；
钉钉新版文档里对应字段写的是 `formComponentValues`（元素带 `id` / `componentType`）。
本地无法判定哪个对，**必须真发一次**才能定（脚本里不做"对错"断言，
只把当前形状锁住，免得它悄悄漂移还看不见）。这一条同步记在交接说明里。
"""

import asyncio
import sys
import time
import types

from sqlalchemy import select, text

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
            form_values=[{"name": "TextField_CHKOA_NO0", "value": "CHKOA-1"}],
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
        "表单值挂在 formValues 上（**键名未与真实响应核过**）",
        isinstance(body.get("formValues"), list),
        "钉钉新版文档写的是 formComponentValues，需真发一次才能定",
    )


async def main() -> int:
    from app.core.config import settings
    from app.modules.customer.model import Customer
    from app.modules.dingtalk import client as client_module
    from app.modules.dingtalk import router as dt_router
    from app.modules.dingtalk import service as dt
    from app.modules.file.model import BusinessFile, FileRecord
    from app.modules.file.storage import absolute_path
    from app.modules.inquiry.model import CustomInquiry
    from app.modules.settings.model import SystemSetting

    stamp = int(time.time())
    object_key = f"{PREFIX}/{stamp}.png"
    fake = FakeClient()

    # 两个 import 点都要打桩：service 在模块级 `from ... import get_client`，
    # router 在函数里再导一次——只改一个的话，另一个还在走真客户端（会真连网）
    dt.get_client = lambda: fake
    original_client_getter = client_module.get_client
    client_module.get_client = lambda: fake

    # 总闸只在本进程内打开——**客户端是假的，不会有任何真实请求**
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

        print("=== 1. 从 CRM 发起：走的是 router（不是直接调 service）===")
        async with SessionLocal() as s:
            admin = (
                await s.execute(select(User).where(User.username == "admin"))
            ).scalars().one()
            user = CurrentUser(admin, permissions=set(), roles=[], data_scope="all")
            resp = await dt_router.start_inquiry_approval(
                inquiry_id=inquiry_id,
                payload=dt_router.StartApproval(),
                request=fake_request(),
                user=user,
                session=s,
            )
        check("接口返回码", resp.get("code"), 0)
        check("外部建单被调用次数", fake.created, 1)
        check("按姓名找人时用的是 CRM 里的姓名", fake.searched_name, "系统管理员")
        check(
            "报文里的发起人是钉钉 userid",
            fake.last_kwargs.get("originator_user_id"),
            DING_USER_ID,
        )
        check_true(
            "发起人不是 CRM 主键",
            fake.last_kwargs.get("originator_user_id") != str(admin.id),
            f"CRM id={admin.id}",
        )

        sent = {
            item["name"]: item["value"] for item in fake.last_kwargs.get("form_values", [])
        }
        check(
            "「提报人」控件填的也是同一个编号",
            sent.get(TEST_CONFIG["originator_component"]),
            DING_USER_ID,
        )
        check(
            "「提报部门」控件填的是部门编号",
            str(sent.get(TEST_CONFIG["dept_component"])),
            str(DING_DEPT_ID),
        )
        check(
            "「对接报价员」控件填的是需求上选的那位",
            sent.get(TEST_CONFIG["quote_owner_component"]),
            DING_QUOTE_ID,
        )
        check(
            "「产品参考图片」填的是上传后的 media_id",
            sent.get(TEST_CONFIG["image_component"]),
            "FAKE-MEDIA-1",
        )
        check_true(
            "图片真的走了上传（没把必填项跳过）", fake.uploaded == 1, f"uploaded={fake.uploaded}"
        )

        print('=== 2. 落库那行：排查"这单谁发起的"只看这一列 ===')
        async with SessionLocal() as s:
            stored = (
                await s.execute(
                    text(
                        "select originator_user_id, status, instance_id from oa_instances "
                        "where inquiry_id = :iid order by id desc limit 1"
                    ),
                    {"iid": inquiry_id},
                )
            ).mappings().first()
        check_true("有落库记录", stored is not None)
        if stored is not None:
            check("库里存的是钉钉 userid", stored["originator_user_id"], DING_USER_ID)
            check("状态", stored["status"], "pending")
            check("实例号", stored["instance_id"], "FAKE-INST-1")
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
        dt.get_client = original_client_getter
        client_module.get_client = original_client_getter
        settings.dingtalk_push_off = True
        await cleanup(object_key)

    print()
    if FAILURES:
        print("FAILED " + str(len(FAILURES)) + " 项：" + "、".join(FAILURES))
        return 1
    print("钉钉发起报文契约回归 全部通过（全程未连钉钉）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
