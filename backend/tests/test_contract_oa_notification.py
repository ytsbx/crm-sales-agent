"""合同审批出结果后：提醒负责人 + 刷新「上传客户回签合同」待办。

守 2026-10-16 补的一条。背景（主人原话）：

    客户盖好合同回传后需要有个待办提醒，直到回签后的文件上传 CRM

待办原先只在**生成合同那一刻**建好，此后任凭审批通过、客户一直不回签，
它都不会再响一声 —— 而"审批刚通过"才是真正该去追的时点（合同获批、客户
手上还没盖章）。原先只有"登记回签件自动完成"和"作废取消"两个时点，中间全程静默。

⚠️ 这段逻辑**只提醒、不改合同状态**：合同要等回签件登记上来才算签成
（`contract/service.sign_document`），审批通过只是"可以寄给客户签了"。
"""
from datetime import UTC, datetime
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock

import pytest

from app.modules.dingtalk import service as oa
from app.modules.notification import service as notification_service
from app.modules.settings import service as settings_service
from app.modules.task.model import Task


def _doc(**over):
    doc = Obj(id=55, doc_no="CT-2026-001", customer_id=9, deleted_at=None, filled_data={})
    for key, value in over.items():
        setattr(doc, key, value)
    return doc


def _task(**over):
    task = Task(
        id=1, title="上传客户回签合同：CT-2026-001", task_type="contract_signed_upload",
        customer_id=9, owner_id=42, priority="normal", status="pending",
        source="system", source_business_type="contract", source_business_id=55,
        source_key="contract:signed-upload:55",
    )
    for key, value in over.items():
        setattr(task, key, value)
    return task


def _session(task):
    """`session.execute(...)` 只被用来取那条待办；给它一个同步形状的结果集。"""
    result = Obj(scalars=lambda: Obj(first=lambda: task))
    return Obj(execute=AsyncMock(return_value=result))


def _spy_notify(monkeypatch):
    sent = []

    async def fake_notify(session, **kwargs):
        sent.append(kwargs)
        return None

    monkeypatch.setattr(notification_service, "notify", fake_notify)
    return sent


@pytest.mark.anyio
async def test_approval_notifies_the_task_owner(monkeypatch):
    sent = _spy_notify(monkeypatch)
    monkeypatch.setattr(settings_service, "get_number", AsyncMock(return_value=7))

    await oa._notify_contract_review_outcome(_session(_task()), _doc(), "approved")

    assert len(sent) == 1
    assert sent[0]["user_id"] == 42          # 待办负责人，就是该去追回签的人
    assert sent[0]["business_type"] == "contract"
    assert sent[0]["business_id"] == 55
    assert "CT-2026-001" in sent[0]["title"]
    assert "已通过" in sent[0]["title"]
    assert "回签" in sent[0]["content"]


@pytest.mark.anyio
async def test_approval_pushes_the_countersign_due_date_forward(monkeypatch):
    """通过后待办要有一个未来的截止日 —— 否则它永远不逾期、也永远排不到前面。"""
    _spy_notify(monkeypatch)
    monkeypatch.setattr(settings_service, "get_number", AsyncMock(return_value=7))
    task = _task()

    await oa._notify_contract_review_outcome(_session(task), _doc(), "approved")

    assert task.due_at is not None
    assert task.due_at > datetime.now(UTC)   # 未来，不是"一出生就逾期"
    assert task.priority == "high"


@pytest.mark.anyio
async def test_rejection_notifies_but_does_not_set_a_chase_deadline(monkeypatch):
    """被驳回时没有"追客户盖章"这件事，不该给待办设一个催办截止日。"""
    sent = _spy_notify(monkeypatch)
    task = _task()

    await oa._notify_contract_review_outcome(_session(task), _doc(), "rejected")

    assert task.due_at is None
    assert len(sent) == 1
    assert "已被驳回" in sent[0]["title"]
    assert "钉钉" in sent[0]["content"]


@pytest.mark.anyio
async def test_no_task_means_no_notification(monkeypatch):
    """待办已经完成/取消（或本来就没建），就不要再打扰人。"""
    sent = _spy_notify(monkeypatch)

    await oa._notify_contract_review_outcome(_session(None), _doc(), "approved")

    assert sent == []


@pytest.mark.anyio
async def test_due_date_follows_the_configured_number_of_days(monkeypatch):
    """天数走配置（contract.countersign_days），不是写死的值。"""
    _spy_notify(monkeypatch)
    get_number = AsyncMock(return_value=3)
    monkeypatch.setattr(settings_service, "get_number", get_number)
    task = _task()

    await oa._notify_contract_review_outcome(_session(task), _doc(), "approved")

    assert get_number.await_args.args[1:] == ("contract", "countersign_days", 7)


# ---------------------------------------------------------------- 轮询集成
#
# 上面几条测的是"提醒长什么样"；下面这几条测**什么时候才提醒**——去重判据
# 必须真的落在 `sync_pending_instances` 里，否则轮询每 3 分钟就会重复打扰人一次。


@pytest.fixture()
def oa_db():
    """给轮询链路用的内存库。

    不能用 conftest 的 `db_session`：那张固定表清单里没有
    `contract_documents` / `tasks` / `notifications`，而这段逻辑三个都要读。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.core.base import Base
    from app.modules.contract.model import ContractDocument, ContractTemplate
    from app.modules.customer.model import Contact, Customer
    from app.modules.dingtalk.model import OaInstance
    from app.modules.notification.model import Notification
    from app.modules.settings.model import SystemSetting
    from app.modules.task.model import Task
    from tests.conftest import _register_sqlite_bigint_as_integer

    _register_sqlite_bigint_as_integer()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        model.__table__ for model in (
            Customer, Contact, ContractTemplate, ContractDocument,
            Task, Notification, OaInstance,
            # `notification.notify()` 要读通知渠道配置，缺这张表会报
            # "no such table: system_settings"
            SystemSetting,
        )
    ])
    with Session(engine) as session:
        yield session


def _seed(session):
    """一个客户 + 一份客户起草合同 + 那条回签待办 + 一条待审批的 OA 行。"""
    from datetime import UTC, datetime

    from app.modules.contract.model import ContractDocument, ContractTemplate
    from app.modules.customer.model import Customer
    from app.modules.dingtalk.model import OaInstance

    session.add(Customer(id=9, name="甲公司", owner_id=42, created_at=datetime.now(UTC)))
    session.add(ContractTemplate(
        id=3, doc_type="contract", name="销售合同", version=1, body="x", enabled=True,
        created_at=datetime.now(UTC),
    ))
    session.add(ContractDocument(
        id=55, doc_no="CT-2026-001", doc_type="contract", title="销售合同",
        customer_id=9, template_id=3, content_snapshot="正文", status="draft",
        filled_data={"_source_type": "customer_drafted"},
        created_at=datetime.now(UTC),
    ))
    session.add(_task())
    session.add(OaInstance(
        id=1, customer_id=9, inquiry_id=55, inquiry_version=1, oa_type="contract",
        idempotency_key="55:1:contract:1", submit_round=1, process_code="PROC-C",
        originator_user_id="ding-1", instance_id="INST-1", status="pending",
        created_at=datetime.now(UTC),
    ))
    session.commit()


def _fake_client(result: str):
    """钉钉替身：只回答"这单审批完了，结果是 agree / refuse"。"""
    return Obj(get_process_instance=AsyncMock(return_value={"status": "COMPLETED", "result": result}))


@pytest.fixture(autouse=True)
def _open_push_gate(monkeypatch):
    """推送总闸默认关着（`DINGTALK_PUSH_OFF`），关着时 `sync_pending_instances`
    第一行就返回"没检查任何单"。要验轮询行为，得先把闸门打开。"""
    from app.modules.dingtalk import service as _oa

    monkeypatch.setattr(_oa.app_settings, "dingtalk_push_off", False)


@pytest.mark.anyio
async def test_polling_notifies_once_and_never_repeats(oa_db, monkeypatch):
    """同一状态被轮询回写多次，只能提醒一次——否则每 3 分钟打扰一次。"""
    from tests.conftest import SyncSessionAsAsync
    from app.modules.notification.model import Notification

    _seed(oa_db)
    monkeypatch.setattr(oa, "get_client", lambda: _fake_client("agree"))
    monkeypatch.setattr(settings_service, "get_number", AsyncMock(return_value=7))
    session = SyncSessionAsAsync(oa_db)

    await oa.sync_pending_instances(session)
    await oa.sync_pending_instances(session)
    await oa.sync_pending_instances(session)

    rows = oa_db.query(Notification).filter(Notification.business_id == 55).all()
    assert len(rows) == 1


@pytest.mark.anyio
async def test_polling_refreshes_the_countersign_task(oa_db, monkeypatch):
    from datetime import UTC, datetime

    from tests.conftest import SyncSessionAsAsync

    _seed(oa_db)
    monkeypatch.setattr(oa, "get_client", lambda: _fake_client("agree"))
    monkeypatch.setattr(settings_service, "get_number", AsyncMock(return_value=7))
    before = oa_db.query(Task).filter(Task.id == 1).one()
    assert before.due_at is None

    await oa.sync_pending_instances(SyncSessionAsAsync(oa_db))

    tasks = oa_db.query(Task).filter(Task.id == 1).all()
    assert len(tasks) == 1                       # 刷新既有待办，不另建一条
    assert tasks[0].due_at is not None
    assert tasks[0].due_at > datetime.now(UTC)


@pytest.mark.anyio
async def test_rejected_approval_also_notifies(oa_db, monkeypatch):
    from tests.conftest import SyncSessionAsAsync
    from app.modules.contract.model import ContractDocument
    from app.modules.notification.model import Notification

    _seed(oa_db)
    monkeypatch.setattr(oa, "get_client", lambda: _fake_client("refuse"))
    monkeypatch.setattr(settings_service, "get_number", AsyncMock(return_value=7))

    await oa.sync_pending_instances(SyncSessionAsAsync(oa_db))

    rows = oa_db.query(Notification).filter(Notification.business_id == 55).all()
    assert len(rows) == 1
    assert "驳回" in rows[0].title
    # 被驳回也**不改合同状态**：合同要等回签件登记才算签成
    doc = oa_db.query(ContractDocument).filter(ContractDocument.id == 55).one()
    assert doc.status == "draft"


@pytest.mark.anyio
async def test_finished_contract_does_not_get_a_chase_deadline(oa_db, monkeypatch):
    """被驳回时没有"追客户盖章"这件事，不该给待办设催办截止日。"""
    from tests.conftest import SyncSessionAsAsync

    _seed(oa_db)
    monkeypatch.setattr(oa, "get_client", lambda: _fake_client("refuse"))
    monkeypatch.setattr(settings_service, "get_number", AsyncMock(return_value=7))

    await oa.sync_pending_instances(SyncSessionAsAsync(oa_db))

    assert oa_db.query(Task).filter(Task.id == 1).one().due_at is None
