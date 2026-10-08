#!/usr/bin/env python
"""§8.5 附件必须跟随来源模块的查看/写入授权（真接口 + 真库）。

守的问题（交接说明 §8.5）
------------------------
1. `visible_object(product, 任意编号)` 既不查产品也不查 `product:view`，直接放行：
   给不存在 / 已软删的产品挂附件也能成功，脏关联落库后谁也说不清它挂在哪。
2. 文件路由只守 `file:view` / `file:manage`：客户/报价等分支不看来源模块权限。
   有 `file:view` 而**没有** `quote:view` 的人猜一个 file_id 就能从通用入口
   （`GET /files/{id}`、`/business/quote/{id}/files`）把报价附件拿走；
   写入同理——看得见不等于能改。
3. 已软删的对象没有一致过滤：删掉的客户 / 产品上的附件照样能下载。
4. 关联变更（带目标上传 / 挂载 / 解绑）要能从审计里查出"挂到了哪条业务记录"。

同时守住两条**不能倒退**的既有口径：
- 只获报价权限的角色仍能合法下载**自己**报价的附件（收紧不能收成谁都下不了）；
- 原件保护（signed / generated 既不能删也不能解绑）仍要走 422，不能被新的写入闸门
  提前改成 403 —— 那样"保护还在不在"就看不出来了。

跑法（必须显式给 API_BASE，指到一次性隔离库；本套件会真的上传文件）：

    cd backend
    API_BASE=http://127.0.0.1:8015/api/v1 DATABASE_URL=...crm_iso_xxx \
      PYTHONPATH=. .venv/Scripts/python.exe scripts/check_attachment_business_auth.py

或直接由 `ops/iso_checks.ps1 -Db crm_iso_x -Port 8015 -Suites check_attachment_business_auth` 驱动。
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import select, text
from _test_support import require_api_base, require_isolated_db

require_isolated_db()

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.modules.customer.model import Customer
from app.modules.file.model import FileRecord
from app.modules.file import storage
from app.modules.quote.model import Quote
from app.modules.user.model import Role, User, role_permissions

BASE = require_api_base()
PREFIX = "CHKATTAUTH"
STAMP = str(int(time.time()))
FAILURES: list[str] = []

#: 被拒的正常表现：403（权限/范围拒绝）或 404（不暴露存在性）
DENIED = (403, 404)
#: 入参/业务规则拒绝：400（参数错误）或 422（业务规则）
REJECTED = (400, 422)

#: ⚠️ 必须**显式**给 API_BASE：不给就拒跑。
#: 本套件会真的上传文件、真的建用户/报价夹具；忘了传就会打到**开发后端**（默认 8000）上，
#: 夹具建在隔离库、写入落在开发库，两边对不上还污染真数据。
if not BASE:
    raise SystemExit("必须显式设置 API_BASE（本套件会真的上传与建夹具，不能默认打到开发后端 8000）")
if "8000" in BASE:
    raise SystemExit(f"API_BASE 指向 8000（开发后端）很可能是误传：{BASE}")


def check(label: str, actual, expected) -> None:
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def check_denied(label: str, status: int) -> None:
    print(f'  {"OK  " if status in DENIED else "FAIL"} {label}: HTTP {status}（期望 403/404）')
    if status not in DENIED:
        FAILURES.append(label)


def call(method: str, path: str, token: str | None = None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw.decode() or "{}")
        except Exception:  # noqa: BLE001
            return error.code, {}


def call_status(method: str, path: str, token: str) -> int:
    """只要状态码（下载返回的是二进制，不能按 JSON 解）。"""
    request = urllib.request.Request(BASE + path, method=method)
    request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def upload(token: str, filename: str, *, business_type=None, business_id=None, category=None):
    """走**真实上传接口**（不直接往 business_files 插行——那正是要堵的缺口）。"""
    boundary = "----chkattauth" + uuid4().hex
    chunks: list[bytes] = [
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{filename}"\r\nContent-Type: image/png\r\n\r\n'
        ).encode(),
        b"\x89PNG\r\n\x1a\n" + os.urandom(32),
        b"\r\n",
    ]
    for name, value in (
        ("business_type", business_type),
        ("business_id", business_id),
        ("category", category),
    ):
        if value is not None:
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{BASE}/files/upload",
        data=b"".join(chunks),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw or b"{}")
        except Exception:  # noqa: BLE001
            return error.code, {}


def login(username: str, password: str) -> str:
    _, result = call("POST", "/auth/login", body={"username": username, "password": password})
    if result.get("code") != 0:
        raise SystemExit(f"登录失败（{username}）：{result.get('message')}")
    return result["data"]["access_token"]


def upload_id(token: str, filename: str, **kwargs) -> int:
    """上传并断言成功，返回文件 id。"""
    status, result = upload(token, filename, **kwargs)
    if status != 200:
        raise SystemExit(f"夹具上传失败 {filename}：HTTP {status} {result}")
    return result["data"]["id"]


def _in(values: list[int]) -> str:
    """拼一个 id 列表给 raw SQL 用（值全部来自数据库/自造，均为整数）。"""
    return "(" + ",".join(str(int(v)) for v in values) + ")" if values else "(-1)"


async def cleanup() -> None:
    """自底向上清干净：审计 → 关联 → 文件（含磁盘）→ 报价 → 客户/产品 → 角色/用户。"""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text("select id, object_key from files where file_name like :p"),
                {"p": f"{PREFIX}%"},
            )
        ).all()
        file_ids = [row[0] for row in rows]
        object_keys = [row[1] for row in rows]
        customers = [
            row[0]
            for row in (
                await session.execute(
                    text("select id from customers where name like :p"), {"p": f"{PREFIX}%"}
                )
            ).all()
        ]
        products = [
            row[0]
            for row in (
                await session.execute(
                    text("select id from products where name like :p"), {"p": f"{PREFIX}%"}
                )
            ).all()
        ]
        quotes = [
            row[0]
            for row in (
                await session.execute(
                    text("select id from quotes where quote_no like :p"), {"p": f"{PREFIX}%"}
                )
            ).all()
        ]
        targets = _in(customers + products + quotes)
        await session.execute(
            text(
                "delete from audit_logs where (business_type = 'file' and business_id in "
                f"{_in(file_ids)}) or (business_type in ('customer','product','quote') "
                f"and business_id in {targets})"
            )
        )
        await session.execute(
            text("delete from business_files where file_id in " + _in(file_ids))
        )
        await session.execute(text("delete from files where id in " + _in(file_ids)))
        if quotes:
            await session.execute(
                text(f"update quotes set current_version_id = null where id in {_in(quotes)}")
            )
            await session.execute(
                text(f"delete from quote_versions where quote_id in {_in(quotes)}")
            )
            await session.execute(text(f"delete from quotes where id in {_in(quotes)}"))
        await session.execute(
            text(f"delete from customers where id in {_in(customers)}")
        )
        await session.execute(text(f"delete from products where id in {_in(products)}"))
        await session.execute(
            text(
                "delete from user_roles where user_id in "
                "(select id from users where username like :u)"
            ),
            {"u": f"{PREFIX.lower()}%"},
        )
        # role_permissions 先删：roles 上有外键，顺序反了会 IntegrityError
        await session.execute(
            text(
                "delete from role_permissions where role_id in "
                "(select id from roles where code like :c)"
            ),
            {"c": f"{PREFIX}%"},
        )
        await session.execute(text("delete from roles where code like :c"), {"c": f"{PREFIX}%"})
        await session.execute(
            text("delete from users where username like :u"), {"u": f"{PREFIX.lower()}%"}
        )
        await session.commit()

    for key in object_keys:
        try:
            storage.delete_object(key)
        except Exception:  # noqa: BLE001 —— 盘上残留不该让清理整体失败
            pass


async def build_fixtures() -> dict:
    """造两套口径的账号（各自的数据范围都是 self）、一个报价和两个产品。

    - `limited`：有文件权限 + `customer:view` + `product:view`，**没有** `quote:view`、
      **没有** `customer:update`。用来验"只认 file:* 的后门关掉了没"。
    - `quote_only`：只有 `file:view` + `quote:view`。用来验"只获报价权限的人仍能合法
      下载自己的报价附件"，同时拿不到产品附件。
    """
    from app.modules.user.model import Permission

    async with SessionLocal() as session:
        permission_ids = {
            code: pid
            for code, pid in (
                await session.execute(select(Permission.code, Permission.id))
            ).all()
        }
        users: dict[str, User] = {}
        for username, codes in (
            ("limited", ["file:view", "file:manage", "customer:view", "product:view"]),
            ("quote_only", ["file:view", "quote:view"]),
        ):
            role = Role(
                code=f"{PREFIX}{username.upper()}{STAMP}",
                name=f"{PREFIX}{username}角色",
                data_scope="self",
            )
            user = User(
                name=f"{PREFIX}{username}-{STAMP}",
                username=f"{PREFIX.lower()}_{username}_{STAMP}",
                password_hash=hash_password("123456"),
                status="active",
            )
            session.add_all([role, user])
            await session.flush()
            for code in codes:
                await session.execute(
                    role_permissions.insert().values(
                        role_id=role.id, permission_id=permission_ids[code]
                    )
                )
            await session.execute(
                text("insert into user_roles (user_id, role_id) values (:u, :r)"),
                {"u": user.id, "r": role.id},
            )
            users[username] = user

        limited = users["limited"]
        quote_only = users["quote_only"]
        own_customer = Customer(
            name=f"{PREFIX}受限账号客户-{STAMP}", owner_id=limited.id, pool_status="private"
        )
        quote_customer = Customer(
            name=f"{PREFIX}报价客户-{STAMP}", owner_id=quote_only.id, pool_status="private"
        )
        session.add_all([own_customer, quote_customer])
        await session.flush()

        quote = Quote(
            quote_no=f"{PREFIX}{STAMP}",
            customer_id=quote_customer.id,
            owner_id=quote_only.id,
            status="draft",
            created_by=quote_only.id,
            created_at=datetime.now(UTC),
        )
        session.add(quote)
        await session.commit()

        return {
            "limited": limited.username,
            "quote_only": quote_only.username,
            "own_customer": own_customer.id,
            "quote": quote.id,
        }


async def file_exists_on_disk(file_id: int) -> bool:
    async with SessionLocal() as session:
        record = await session.get(FileRecord, file_id)
    return record is not None and storage.absolute_path(record.object_key).exists()


async def count_files_named(filename: str) -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    text("select count(*) from files where file_name = :n"), {"n": filename}
                )
            ).scalar_one()
        )


async def audit_rows(action: str, business_type: str, business_id: int) -> list[AuditLog]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == action,
                        AuditLog.business_type == business_type,
                        AuditLog.business_id == business_id,
                    )
                )
            ).scalars().all()
        )


async def file_row(file_id: int) -> dict:
    """读文件记录的两个关键字段：给人看的名字、和磁盘上的位置。

    改名的断言要**同时**看这两个 —— 只动前者、后者一个字不变才叫"只改展示名"；
    `object_key` 一动就意味着要搬文件，搬到一半失败会留下"记录指着不存在的路径"。
    """
    async with SessionLocal() as session:
        record = await session.get(FileRecord, file_id)
    if record is None:
        return {}
    return {"file_name": record.file_name, "object_key": record.object_key}


async def count_links(file_id: int) -> int:
    async with SessionLocal() as session:
        return int(
            (
                await session.execute(
                    text("select count(*) from business_files where file_id = :f"),
                    {"f": file_id},
                )
            ).scalar_one()
        )


async def main() -> int:
    import app.main

    _ = app.main  # 保证所有模型都注册进 metadata（下面要直接建夹具）

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    db_name = (db.path or "").lstrip("/").lower()
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}, db
    # 开发库 crm_sales_agent 一律拒跑；只接受一次性隔离库（与 iso_checks.ps1 同一命名规则）
    assert (
        db_name.startswith(("crm_iso", "crm_check")) or "test" in db_name or os.getenv("CI") == "true"
    ), db
    assert settings.wecom_push_off and settings.dingtalk_push_off and not settings.scheduler_enabled, (
        "这条回归只能在推送全关的隔离库跑"
    )

    await cleanup()  # 先清，避免上次中断留下的残渣影响断言
    ids = await build_fixtures()

    admin = login("admin", "admin123")
    limited = login(ids["limited"], "123456")
    quote_only = login(ids["quote_only"], "123456")
    quote_id = ids["quote"]
    own_customer = ids["own_customer"]

    print(f"夹具：报价 #{quote_id}（归 quote_only）、客户 #{own_customer}（归 limited）\n")

    # ---------------------------------------------------------------- 1
    print("=== 1. 不存在 / 已删除的产品不能挂文件（修前：任意编号直接放行）===")
    bare_file = upload_id(admin, f"{PREFIX}-bare.png")
    status, result = call(
        "POST", "/products/999999999/files?file_id=" + str(bare_file), token=admin
    )
    check("给不存在的产品挂附件被拒", status, 404)
    denied_upload_name = f"{PREFIX}-ghost.png"
    status, result = upload(
        admin, denied_upload_name, business_type="product", business_id=999999999
    )
    check_denied("上传到不存在的产品被拒", status)
    check("被拒的上传没有留下文件登记", await count_files_named(denied_upload_name), 0)

    product = call("POST", "/products", token=admin, body={"name": f"{PREFIX}产品-{STAMP}"})[1]
    product_id = product["data"]["id"]
    p2 = call("POST", "/products", token=admin, body={"name": f"{PREFIX}待删产品-{STAMP}"})[1]
    p2_id = p2["data"]["id"]
    p2_file = upload_id(admin, f"{PREFIX}-p2.png", business_type="product", business_id=p2_id)
    check("对照：正常产品能挂附件且能读", call_status("GET", f"/files/{p2_file}", admin), 200)

    check("软删产品", call("DELETE", f"/products/{p2_id}", token=admin)[0], 200)
    status, result = call(
        "POST", f"/business/product/{p2_id}/files?file_id={bare_file}", token=admin
    )
    check_denied("已删除的产品不能再挂附件（通用入口）", status)
    status, result = call("POST", f"/products/{p2_id}/files?file_id={bare_file}", token=admin)
    check("已删除的产品走产品附件入口也被拒", status, 404)
    status, result = upload(
        admin, f"{PREFIX}-deleted.png", business_type="product", business_id=p2_id
    )
    check_denied("上传到已删除的产品被拒", status)
    check_denied("已删除产品上的附件不再可见", call_status("GET", f"/files/{p2_file}", admin))
    check_denied(
        "已删除产品的附件清单也不给列",
        call("GET", f"/business/product/{p2_id}/files", token=admin)[0],
    )

    # ---------------------------------------------------------------- 2
    print("\n=== 2. 通用入口不再只认 file:*（有文件权限、没该模块权限拿不到）===")
    quote_file = upload_id(
        admin, f"{PREFIX}-quote.png", business_type="quote", business_id=quote_id
    )
    customer_file = upload_id(
        admin, f"{PREFIX}-cust.png", business_type="customer", business_id=own_customer
    )
    check_denied(
        "有 file:view 没 quote:view：列不出报价附件",
        call("GET", f"/business/quote/{quote_id}/files", token=limited)[0],
    )
    check_denied("有 file:view 没 quote:view：取不到报价附件本体",
                 call_status("GET", f"/files/{quote_file}", limited))
    check_denied("下载同样被拒（不是只挡列表）",
                 call_status("GET", f"/files/{quote_file}/download", limited))
    check_denied(
        "上传到报价也被拒",
        upload(limited, f"{PREFIX}-up-quote.png", business_type="quote", business_id=quote_id)[0],
    )
    check_denied(
        "把报价附件挂到报价上也被拒",
        call("POST", f"/business/quote/{quote_id}/files?file_id={quote_file}", token=limited)[0],
    )
    check("对照：有 customer:view 且范围内，客户附件清单能看",
          call("GET", f"/business/customer/{own_customer}/files", token=limited)[0], 200)
    check("对照：范围内的客户附件本人能读",
          call_status("GET", f"/files/{customer_file}", limited), 200)

    # ---------------------------------------------------------------- 3
    print("\n=== 3. 挂载/上传要目标模块的**写入**授权（看得见 ≠ 能改）===")
    denied_up = f"{PREFIX}-up-cust.png"
    status, result = upload(
        limited, denied_up, business_type="customer", business_id=own_customer
    )
    check_denied("没有 customer:update：不能往客户上传附件", status)
    check("被拒的上传没有留下文件登记", await count_files_named(denied_up), 0)
    status, result = call(
        "POST", f"/business/customer/{own_customer}/files?file_id={customer_file}", token=limited
    )
    check_denied("没有 customer:update：不能往客户挂附件", status)
    check("被拒的挂载没有留下关联", await count_links(customer_file), 1)

    # 产品附件的写入授权 2026-10-07 收紧为 product:manage（业务口径已确认）：
    # 原来只守 file:manage，于是"有文件管理权、但不管产品"的人也能改产品上的资料。
    # limited 角色有 file:manage + product:view，唯独没有 product:manage —— 正是这一档。
    limited_file = upload_id(limited, f"{PREFIX}-limited.png")
    status, result = call(
        "POST", f"/products/{product_id}/files?file_id={limited_file}", token=limited
    )
    check_denied("没有 product:manage：不能给产品挂附件（哪怕有 file:manage）", status)
    check("被拒的挂载没有留下关联（产品）", await count_links(limited_file), 0)
    # 对照：管理员（有 product:manage）挂**自己上传的**文件 —— 证明上一句拒的是权限，
    # 不是接口坏了。注意 `can_access_file` 对"还没挂过"的裸文件只放行**上传者本人**，
    # 所以这里必须是 admin 自己传的那份，不能拿 limited 那份（那会 403，测的就不是权限了）。
    admin_file = upload_id(admin, f"{PREFIX}-admin.png")
    status, result = call(
        "POST", f"/products/{product_id}/files?file_id={admin_file}", token=admin
    )
    check("对照：有 product:manage 的人能给产品挂附件", status, 200)
    check("对照：挂上后能读（产品附件对 product:view 的人可见）",
          call_status("GET", f"/files/{admin_file}", limited), 200)

    # ---------------------------------------------------------------- 4
    print("\n=== 4. 只获报价权限的角色：自己的报价附件仍能合法下载（不能收成谁都下不了）===")
    rows = call("GET", f"/business/quote/{quote_id}/files", token=quote_only)[1]
    listed = {row.get("id") for row in (rows.get("data") or [])}
    check_true("报价附件清单能列出刚上传的那份", quote_file in listed, rows)
    check("附件详情可读", call_status("GET", f"/files/{quote_file}", quote_only), 200)
    check("附件可下载", call_status("GET", f"/files/{quote_file}/download", quote_only), 200)
    check_denied(
        "但拿不到产品附件（没有 product:view）",
        call_status("GET", f"/files/{admin_file}", quote_only),
    )

    # ---------------------------------------------------------------- 5
    print("\n=== 5. 关联变更必须留审计（含挂到了哪条业务记录）===")
    attach_file = upload_id(admin, f"{PREFIX}-audit.png")
    status, result = call(
        "POST", f"/business/customer/{own_customer}/files?file_id={attach_file}", token=admin
    )
    check("挂载成功", status, 200)
    attach_link = result["data"]["business_file_id"]
    attach_audits = await audit_rows("attach", "customer", own_customer)
    check_true("留下 action=attach 的审计", bool(attach_audits), attach_audits)
    check_true(
        "审计里带着挂上去的 file_id",
        any((row.after_data or {}).get("file_id") == attach_file for row in attach_audits),
        [row.after_data for row in attach_audits],
    )

    check("解绑成功", call("DELETE", f"/business-files/{attach_link}", token=admin)[0], 200)
    detach_audits = await audit_rows("detach", "customer", own_customer)
    check_true("留下 action=detach 的审计", bool(detach_audits), detach_audits)
    check_true(
        "解绑审计里带着 file_id",
        any((row.before_data or {}).get("file_id") == attach_file for row in detach_audits),
        [row.before_data for row in detach_audits],
    )

    upload_audits = await audit_rows("upload", "file", customer_file)
    check_true(
        "带目标上传的审计记下了目标业务对象（target/target_id）",
        any(
            (row.after_data or {}).get("target") == "customer"
            and (row.after_data or {}).get("target_id") == own_customer
            for row in upload_audits
        ),
        [row.after_data for row in upload_audits],
    )

    # ---------------------------------------------------------------- 6
    print("\n=== 6. 已软删对象上的附件不再可见（未一致过滤已删除对象）===")
    doomed = upload_id(
        admin, f"{PREFIX}-doomed.png", business_type="customer", business_id=own_customer
    )
    check("准备条件：删除前能读", call_status("GET", f"/files/{doomed}", admin), 200)
    check("软删客户", call("DELETE", f"/customers/{own_customer}", token=admin)[0], 200)
    check_denied("已删除客户上的附件不再可读", call_status("GET", f"/files/{doomed}", admin))
    check_denied(
        "已删除客户的附件清单也不给列",
        call("GET", f"/business/customer/{own_customer}/files", token=admin)[0],
    )

    # ---------------------------------------------------------------- 7
    print("\n=== 7. 原件保护不能倒退（写入闸门不许把 422 变成 403）===")
    generated = upload_id(
        admin, f"{PREFIX}-gen.pdf", business_type="product", business_id=product_id,
        category="generated",
    )
    check("准备条件：生成稿已落盘", await file_exists_on_disk(generated), True)
    rows = call("GET", f"/business/product/{product_id}/files", token=admin)[1]
    generated_link = next(
        (row["business_file_id"] for row in (rows.get("data") or []) if row.get("id") == generated),
        None,
    )
    check_true("找得到生成稿那条关联（下面两条断言才有意义）", generated_link is not None, rows)
    if generated_link is not None:
        # 422 而不是 403：403 意味着被新的写入闸门拦住了，"原件保护还在不在"就看不出来。
        check(
            "生成稿原件不能解绑",
            call("DELETE", f"/business-files/{generated_link}", token=admin)[0],
            422,
        )
        after = call("GET", f"/business/product/{product_id}/files", token=admin)[1]
        check_true(
            "被拒之后关联还在（失败路径不改状态）",
            any(
                row.get("business_file_id") == generated_link
                for row in (after.get("data") or [])
            ),
            after,
        )
    check("生成稿原件不能被通用删除", call("DELETE", f"/files/{generated}", token=admin)[0], 422)

    # ---------------------------------------------------------------- 改名（2026-10-08）
    print("\n=== 改名：只改展示名；合同原件不许改 ===")
    rename_id = upload_id(
        admin, f"{PREFIX}-rename-me.png", business_type="product", business_id=product_id
    )
    before_row = await file_row(rename_id)
    check("准备条件：磁盘上真有这份文件", await file_exists_on_disk(rename_id), True)

    status, body = call(
        "PATCH", f"/files/{rename_id}", token=admin,
        body={"file_name": "  ZX-6040 塑料周转箱 规格书.pdf  "},
    )
    check("普通附件改名 → 200", status, 200)
    after_row = await file_row(rename_id)
    check("名字改了（首尾空白顺手去掉）", after_row["file_name"], "ZX-6040 塑料周转箱 规格书.pdf")
    check("磁盘位置一个字没动", after_row["object_key"], before_row["object_key"])
    check("文件内容还在原处", await file_exists_on_disk(rename_id), True)
    rows = await audit_rows("rename", "file", rename_id)
    check("写了一条改名审计", len(rows), 1)
    check_true(
        "审计里记下了改之前的名字（只记新名字就查不出被改成了什么）",
        bool(rows) and (rows[0].before_data or {}).get("file_name") == before_row["file_name"],
        rows[0].before_data if rows else None,
    )

    check(
        "改成同一个名字 → 200（幂等，不报错）",
        call("PATCH", f"/files/{rename_id}", token=admin,
             body={"file_name": "ZX-6040 塑料周转箱 规格书.pdf"})[0],
        200,
    )
    check(
        "名字没变就不写审计（点了保存却什么都没改，不该在流水里留一条）",
        len(await audit_rows("rename", "file", rename_id)),
        1,
    )

    for label, bad in (
        ("空", ""),
        ("全空白", "   "),
        ("超过 255 字", "长" * 256),
        ("带换行", "abc\ndef.pdf"),
    ):
        check(
            f"名字{label} → 400",
            call("PATCH", f"/files/{rename_id}", token=admin, body={"file_name": bad})[0],
            400,
        )
    check(
        "上面几次被拒之后，名字一个字没动",
        (await file_row(rename_id))["file_name"],
        "ZX-6040 塑料周转箱 规格书.pdf",
    )

    status, body = call(
        "PATCH", f"/files/{generated}", token=admin, body={"file_name": "改个名试试.pdf"}
    )
    check("生成稿原件改名 → 422（与「不可删除」同一份判据）", status, 422)
    check_true(
        "拒绝原因说清它是原件",
        "原件" in (body.get("message") or ""),
        body.get("message"),
    )
    check(
        "被拒之后原件的名字没动",
        (await file_row(generated))["file_name"],
        f"{PREFIX}-gen.pdf",
    )

    # 用 admin 传（quote_only 只有 file:view，传不了）—— 这份文件挂在**别人的报价**上，
    # 正是下面"看不到这个对象就改不了它的附件名"要用的靶子。
    quote_rename_id = upload_id(
        admin, f"{PREFIX}-quote-rename.png", business_type="quote", business_id=quote_id
    )
    check_denied(
        "只有 file:view 的人不能改名（与删除同一档权限）",
        call("PATCH", f"/files/{quote_rename_id}", token=quote_only, body={"file_name": "偷改.pdf"})[0],
    )
    check(
        "权限不足时名字没被动",
        (await file_row(quote_rename_id))["file_name"],
        f"{PREFIX}-quote-rename.png",
    )
    check_denied(
        "看不到那个业务对象的人也不能改它的附件名",
        call("PATCH", f"/files/{quote_rename_id}", token=limited, body={"file_name": "偷改2.pdf"})[0],
    )
    check(
        "被拒之后名字依旧是原来的",
        (await file_row(quote_rename_id))["file_name"],
        f"{PREFIX}-quote-rename.png",
    )

    await cleanup()

    print()
    if FAILURES:
        print(f"✗ 失败 {len(FAILURES)} 项：" + "；".join(FAILURES))
        return 1
    print("✓ 全部通过：附件跟随来源模块的查看/写入授权，原件保护与既有共享口径未倒退")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
