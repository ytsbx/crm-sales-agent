"""归档原件沿用档案当前权限；真实内存 SQL，不连接生产。"""

import asyncio
from datetime import UTC, datetime

import pytest

from conftest import SyncSessionAsAsync, make_user
from app.core.base import Base
from app.modules.bizdoc.model import BizDoc, BizDocTemplate
from app.modules.file.access import can_access_file
from app.modules.file.model import BusinessFile, FileRecord
from app.modules.customer.model import Customer
from app.modules.user.model import Department, User


@pytest.fixture
def archive(db_session):
    Base.metadata.create_all(db_session.bind, tables=[
        BizDocTemplate.__table__, BizDoc.__table__,
        FileRecord.__table__, BusinessFile.__table__,
    ])
    file = FileRecord(id=1, object_key="isolated/original", file_name="original.xlsx", uploaded_by=101)
    doc = BizDoc(id=1, doc_no="ISO-1", doc_type="quote_sheet", title="隔离档案",
                 owner_id=101, template_id=1, input_snapshot={}, content_sha256="a" * 64,
                 file_id=1, created_at=datetime.now(UTC))
    db_session.add_all([file, doc])
    db_session.commit()
    return SyncSessionAsAsync(db_session), doc


@pytest.mark.parametrize("doc_type,permission", [
    ("quote_sheet", "quote:view"), ("order_sheet", "order:view"),
    ("sample_request", "sample:view"),
])
def test_archive_transfer_and_revoked_permission(archive, doc_type, permission):
    session, doc = archive
    doc.doc_type = doc_type
    old = make_user(101, permissions=[permission])
    new = make_user(102, permissions=[permission])
    assert asyncio.run(can_access_file(session, old, 1))
    assert not asyncio.run(can_access_file(session, new, 1))
    doc.owner_id = 102
    assert not asyncio.run(can_access_file(session, old, 1))
    assert asyncio.run(can_access_file(session, new, 1))
    assert not asyncio.run(can_access_file(session, make_user(102), 1))


@pytest.mark.parametrize("scope,owner,allowed", [
    ("all", 102, True), ("all", None, True), ("self", None, False),
])
def test_archive_existing_scope(archive, scope, owner, allowed):
    session, doc = archive
    doc.owner_id = owner
    user = make_user(101, permissions=["quote:view"], data_scope=scope)
    assert asyncio.run(can_access_file(session, user, 1)) is allowed


def test_archive_keeps_explicit_shared_link(archive):
    session, doc = archive
    doc.owner_id = 102
    # 任一可见挂载仍可共享，不要求每一处关联都可见。
    session._session.add(Customer(id=9, name="共享客户", owner_id=101))
    session._session.add(BusinessFile(business_type="customer", business_id=9, file_id=1))
    assert asyncio.run(can_access_file(session, make_user(101, permissions=["customer:view"]), 1))


@pytest.mark.parametrize("scope,allowed", [("department", False), ("department_and_sub", True)])
def test_archive_department_scope(archive, scope, allowed):
    session, doc = archive
    session._session.add_all([
        Department(id=1, name="主管部门"),
        Department(id=2, name="下级部门", parent_id=1),
        User(id=102, username="iso-owner", name="下级负责人", password_hash="x", department_id=2),
    ])
    doc.owner_id = 102
    user = make_user(101, permissions=["quote:view"], data_scope=scope)
    user.department_id = 1
    assert asyncio.run(can_access_file(session, user, 1)) is allowed


def test_genuinely_unlinked_file_keeps_uploader_rule(archive):
    session, doc = archive
    doc.file_id = None
    assert asyncio.run(can_access_file(session, make_user(101), 1))
    assert not asyncio.run(can_access_file(session, make_user(102), 1))
    assert not asyncio.run(can_access_file(session, make_user(101), 999))
