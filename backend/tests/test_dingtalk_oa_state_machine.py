"""第七批 7.7 / 7.8：OA「状态 → 允许动作」与"结果未知"的并发核定。

## 为什么用真 service 函数 + 假钉钉 + 文件版 SQLite

交接文档 §6 给的证据方式是「实际函数 + mock 外部」。这两条的缺陷都长在
**真函数 + 真 SQL**上：把 session 换成 mock 等于自己把 SQL 重写一遍，
"并发两条连接抢同一行"根本测不出来。所以：

- 外部调用全部打桩（`FakeClient`），全程不连钉钉、不发任何真实请求；
- 用**文件版** SQLite 而不是内存库：内存库的连接各自独立，两个连接的并发
  测试必须落在同一个文件上；
- 真 PostgreSQL 的锁行为与重启恢复另见
  `backend/scripts/check_dingtalk_oa_state_machine.py`（SQLite 不证明 PG 的行锁）。

## 这些用例在修复前应当是红的

- 7.7：`resubmit=True` 对 pending/approved 也能开新一轮（会多建外部实例）；
  超时被记成 `failed` 而同轮重试直接返回 failed（死路）；核定入口只认 needs_review。
- 7.8：两个连接同时核定 `needs_review` 会各自调一次外部创建；认领任意字符串即采纳。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from tests.conftest import (
    SyncSessionAsAsync,
    _register_sqlite_bigint_as_integer,
    make_user,
)

from app.core.audit import AuditLog
from app.core.config import settings as app_settings
from app.core.errors import AppError, ErrorCode
from app.core.idempotency import RequestKey
from app.modules.dingtalk import service as svc
from app.modules.dingtalk.client import (
    DingTalkError,
    DingTalkNotConfigured,
    DingTalkUnknownOutcome,
)
from app.modules.dingtalk.model import OaInstance, allowed_actions
from app.modules.inquiry.model import CustomInquiry


class SessionAsAsync(SyncSessionAsAsync):
    """conftest 的替身只实现只读方法；核定路径还要 rollback / delete。"""

    async def rollback(self) -> None:
        self._session.rollback()

    async def delete(self, obj) -> None:
        self._session.delete(obj)


def open_session(engine) -> SessionAsAsync:
    # expire_on_commit=False 与生产 SessionLocal 一致：否则 commit 之后
    # 访问 row.status 会触发惰性加载，在异步会话里就是 MissingGreenlet
    return SessionAsAsync(Session(engine, expire_on_commit=False))


@pytest.fixture()
def db(tmp_path):
    """一个建好相关表的文件版 SQLite 引擎（两个连接共用一个库）。"""
    _register_sqlite_bigint_as_integer()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'oa.db'}",
        connect_args={"check_same_thread": False},
    )

    # 不用 `Base.metadata.create_all`：inquiry 模型里 `status` 同时声明了
    # `index=True` 和同名的 `Index`，SQLAlchemy 会生成两条同名的 CREATE INDEX，
    # SQLite 直接报"索引已存在"（那是别的模块，本轮不擅动）。这里按名字去重建表，
    # 效果与生产 DDL（迁移里一个名字只建一次）一致。
    from sqlalchemy.schema import CreateIndex, CreateTable

    tables = [
        OaInstance.__table__,
        RequestKey.__table__,
        CustomInquiry.__table__,
        AuditLog.__table__,
    ]
    with engine.begin() as conn:
        for table in tables:
            conn.execute(CreateTable(table))
            seen: set[str] = set()
            for index in table.indexes:
                if index.name in seen:
                    continue
                seen.add(index.name)
                conn.execute(CreateIndex(index))
    yield engine
    engine.dispose()


@pytest.fixture()
def session(db):
    with Session(db, expire_on_commit=False) as raw:
        yield SessionAsAsync(raw)


@pytest.fixture(autouse=True)
def _push_open(monkeypatch):
    """本文件要走的都是"外部真的被调用"的路径，所以总闸打开——
    但 get_client 在每个用例里都被换成假的，**不会有任何真实请求**。"""
    monkeypatch.setattr(app_settings, "dingtalk_push_off", False)


class FakeClient:
    """假钉钉客户端：只记账，不连网。"""

    def __init__(self) -> None:
        self.calls = 0
        self.get_calls = 0
        self.create_error: BaseException | None = None
        self.get_error: BaseException | None = None
        self.payloads: dict[str, dict] = {}

    async def create_process_instance(self, **_kwargs) -> str:
        self.calls += 1
        if self.create_error is not None:
            raise self.create_error
        return f"FAKE-{self.calls}"

    async def get_process_instance(self, instance_id: str) -> dict:
        self.get_calls += 1
        if self.get_error is not None:
            raise self.get_error
        if instance_id not in self.payloads:
            raise DingTalkError("没有这张单", api="workflow/processInstances:get", http_status=404)
        return self.payloads[instance_id]


@pytest.fixture()
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(svc, "get_client", lambda: client)
    return client


INQUIRY_ID = 7001


def make_row(db, **overrides) -> int:
    """插一行 OA 记录（默认是"结果待人工核对"）。"""
    now = datetime.now(UTC)
    fields = {
        "customer_id": None,
        "inquiry_id": INQUIRY_ID,
        "inquiry_version": 1,
        "oa_type": "inquiry",
        "idempotency_key": f"{INQUIRY_ID}:1:inquiry:1",
        "submit_round": 1,
        "process_code": "PROC-A",
        "originator_user_id": "ding-user-1",
        "form_snapshot": {"formComponentValues": [{"name": "t", "id": "t", "value": "v"}]},
        "status": "needs_review",
        "created_by": 101,
        "created_at": now,
        "last_attempt_at": now,
    }
    fields.update(overrides)
    with Session(db, expire_on_commit=False) as raw:
        row = OaInstance(**fields)
        raw.add(row)
        raw.commit()
        return row.id


def make_inquiry(db, *, inquiry_id: int = INQUIRY_ID, inquiry_no: str = "XQ-7001") -> None:
    with Session(db, expire_on_commit=False) as raw:
        raw.add(
            CustomInquiry(
                id=inquiry_id,
                inquiry_no=inquiry_no,
                title="测试需求",
                version=1,
                status="open",
                created_by=101,
            )
        )
        raw.commit()


def reload_row(db, oa_id: int) -> OaInstance:
    with Session(db, expire_on_commit=False) as raw:
        row = raw.get(OaInstance, oa_id)
        raw.expunge(row)
        return row


def fake_http_request():
    """够 `client_ip()` / `request_key_from()` 用的最小 Request（接口层用例用）。"""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/dingtalk/oa-instances/0/resolve",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
    )


def submit(session, **overrides):
    kwargs = {
        "user": make_user(101),
        "inquiry_id": INQUIRY_ID,
        "inquiry_version": 1,
        "customer_id": None,
        "process_code": "PROC-A",
        "originator_user_id": "ding-user-1",
        "field_map": {"t": "v"},
    }
    kwargs.update(overrides)
    return svc.create_inquiry_instance(session, **kwargs)


# --------------------------------------------------------------------------
# 7.7 状态 → 允许动作：待审批 / 已通过不许"重提另建实例"
# --------------------------------------------------------------------------


def test_action_matrix_separates_retry_from_unknown():
    """口径本身：只有"明确结束"的轮次才允许重提；结果未知只能人工核定。"""
    assert allowed_actions("pending") == ["sync"]
    assert allowed_actions("approved") == []
    assert allowed_actions("rejected") == ["resubmit"]
    assert allowed_actions("withdrawn") == ["resubmit"]
    assert allowed_actions("failed") == ["retry"]
    assert allowed_actions("not_sent") == ["retry"]
    assert "resubmit" not in allowed_actions("needs_review")
    assert "resubmit" not in allowed_actions("submitting")
    # 占用期间除了查询什么都不许做（否则就是第二次外部创建的机会）
    assert allowed_actions("pending", "processing") == ["sync"]
    assert allowed_actions("needs_review", "processing") == []


@pytest.mark.parametrize("status", ["pending", "approved", "needs_review", "submitting"])
def test_resubmit_refused_for_live_or_unknown_rounds(db, fake, status):
    """待审批/已通过/结果未知/发起中：重提必须**被拒且不碰外部**。"""
    oa_id = make_row(db, status=status)
    with Session(db, expire_on_commit=False) as raw:
        s = SessionAsAsync(raw)
        with pytest.raises(AppError) as caught:
            asyncio.run(submit(s, resubmit=True))
    assert caught.value.code == ErrorCode.STATUS_NOT_ALLOWED
    assert fake.calls == 0, "被拒的重提绝不能向钉钉发起"
    assert reload_row(db, oa_id).submit_round == 1, "不能悄悄落一轮新记录"


def test_resubmit_allowed_after_rejected_creates_new_round(db, fake):
    """已驳回 = 旧轮明确结束：重提换一轮、建新实例（文档 §11.3 :152）。"""
    first = make_row(db, status="rejected", instance_id="FAKE-OLD")
    with Session(db, expire_on_commit=False) as raw:
        s = SessionAsAsync(raw)
        row = asyncio.run(submit(s, resubmit=True))
    assert fake.calls == 1
    assert row.submit_round == 2
    assert row.status == "pending"
    assert row.id != first
    assert reload_row(db, first).status == "rejected", "旧轮历史保留，不被改写"


def test_resubmit_allowed_after_withdrawn(db, fake):
    """人工作废（withdrawn）同样允许重提——口径 (b) 已拍板。"""
    make_row(db, status="withdrawn")
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(submit(SessionAsAsync(raw), resubmit=True))
    assert fake.calls == 1
    assert row.submit_round == 2


def test_repeat_click_while_pending_does_not_touch_external(db, fake):
    """待审批时重复点"发起"：复用原记录，外部调用次数原地不动。"""
    oa_id = make_row(db, status="pending", instance_id="FAKE-1")
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(submit(SessionAsAsync(raw)))
    assert fake.calls == 0
    assert row.id == oa_id
    assert row.instance_id == "FAKE-1"


# --------------------------------------------------------------------------
# 7.7 三种后果分开：确定没发出 / 明确失败 / 结果未知
# --------------------------------------------------------------------------


def test_client_timeout_is_unknown_not_failed(db, fake):
    """服务已收但客户端超时：**结果未知**，不是"发起失败"。

    这是修复前最危险的一条：记成 failed 之后同轮重试会直接返回，
    看着像"卡住"，一旦有人把 failed 接上自动重试，就会在钉钉里建出第二张单。
    """
    oa_id = make_row(db, status="not_sent", instance_id=None)
    fake.create_error = DingTalkUnknownOutcome("模拟超时：响应没回来")
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(submit(SessionAsAsync(raw)))
    assert row.status == "needs_review"
    assert "可能已经建了审批单" in (row.error or "")
    assert reload_row(db, oa_id).attempt_count == 1

    # 结果未知时**不许自动重发**：同轮再点一次不会再打钉钉
    fake.create_error = None
    with Session(db, expire_on_commit=False) as raw:
        again = asyncio.run(submit(SessionAsAsync(raw)))
    assert fake.calls == 1, "结果未知的记录不能自动重发"
    assert again.id == oa_id


def test_explicit_rejection_is_failed_and_retryable(db, fake):
    """外部明确拒绝（4xx）：没有建单，同轮重试是安全的——不能是死路。"""
    oa_id = make_row(db, status="not_sent", instance_id=None)
    fake.create_error = DingTalkError("模板不存在", api="workflow/processInstances", http_status=400)
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(submit(SessionAsAsync(raw)))
    assert row.status == "failed"
    assert fake.calls == 1

    fake.create_error = None
    with Session(db, expire_on_commit=False) as raw:
        retried = asyncio.run(submit(SessionAsAsync(raw)))
    assert fake.calls == 2, "明确失败必须能重试（修复前这里直接返回 failed）"
    assert retried.id == oa_id, "同轮重试复用同一行、同一请求号"
    assert retried.status == "pending"
    assert retried.submit_round == 1
    assert reload_row(db, oa_id).attempt_count == 2


def test_local_error_is_not_sent(db, fake):
    """本地就没发出去（缺配置）：记 not_sent，与"被外部拒绝"分开。"""
    make_row(db, status="not_sent", instance_id=None)
    fake.create_error = DingTalkNotConfigured("DINGTALK_APP_KEY")
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(submit(SessionAsAsync(raw)))
    assert row.status == "not_sent"
    assert "没有发出去" in (row.error or "")


def test_gate_closed_does_not_overwrite_previous_outcome(db, fake, monkeypatch):
    """闸门关着时重复点：原样返回，不把"结果未知"覆盖成"未发起"。"""
    oa_id = make_row(db, status="needs_review", error="上次结果不明")
    monkeypatch.setattr(app_settings, "dingtalk_push_off", True)
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(submit(SessionAsAsync(raw)))
    assert row.id == oa_id
    assert row.status == "needs_review"
    assert row.error == "上次结果不明"
    assert fake.calls == 0


# --------------------------------------------------------------------------
# 7.8 认领：先核实模板 / 发起人 / 来源需求，核实不过一律不采纳
# --------------------------------------------------------------------------


ADOPT_PAYLOAD = {
    "processCode": "PROC-A",
    "originatorUserId": "ding-user-1",
    "businessId": str(INQUIRY_ID),
    "title": "定制询价审批",
    "status": "RUNNING",
}


def resolve(session, oa_id, **kwargs):
    row = asyncio.run(session.get(OaInstance, oa_id))
    return svc.resolve_reviewed_instance(session, row, **kwargs)


def test_adopt_accepts_verified_instance(db, fake):
    make_inquiry(db)
    oa_id = make_row(db)
    fake.payloads["DT-OK"] = dict(ADOPT_PAYLOAD)
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(
            resolve(SessionAsAsync(raw), oa_id, action="adopt", instance_id="DT-OK")
        )
    assert row.status == "pending"
    assert row.instance_id == "DT-OK"
    assert row.resolved_action == "adopt"
    assert row.resolve_state == "idle"


@pytest.mark.parametrize(
    "broken,expect",
    [
        ({"processCode": "PROC-B"}, "审批模板对不上"),
        ({"originatorUserId": "ding-user-9"}, "发起人对不上"),
        ({"businessId": "9999"}, "来源需求对不上"),
        ({"processCode": None}, "没有审批模板编号"),
        ({"originatorUserId": None}, "没有发起人"),
        ({"businessId": None}, "没有来源需求编号"),
    ],
)
def test_adopt_refuses_unverified_instance(db, fake, broken, expect):
    """错误模板/发起人/来源需求，或字段根本缺失 → **拒绝采纳**（fail-closed）。"""
    make_inquiry(db)
    oa_id = make_row(db)
    payload = dict(ADOPT_PAYLOAD)
    payload.update(broken)
    fake.payloads["DT-BAD"] = payload
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                resolve(SessionAsAsync(raw), oa_id, action="adopt", instance_id="DT-BAD")
            )
    assert expect in caught.value.message
    after = reload_row(db, oa_id)
    assert after.instance_id is None, "核实不过就不能把单子认下来"
    assert after.status == "needs_review"
    assert after.resolve_state == "idle", "占用必须放掉，否则这张单永远核不了"


def test_adopt_query_failure_keeps_unknown(db, fake):
    """外部查询失败 → 保留"结果未知"，不能因为查不动就默认放行。"""
    oa_id = make_row(db)
    fake.get_error = DingTalkUnknownOutcome("查询超时")
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                resolve(SessionAsAsync(raw), oa_id, action="adopt", instance_id="DT-1")
            )
    assert caught.value.http_status == 502
    assert "仍是「结果待人工核对」" in caught.value.message
    after = reload_row(db, oa_id)
    assert after.status == "needs_review"
    assert after.instance_id is None


def test_adopt_refuses_instance_already_linked_to_other_inquiry(db, fake):
    """一个外部实例只能关联一行：别的需求已经认领过，就不能再认。"""
    make_inquiry(db)
    taken = make_row(db, inquiry_id=8002, idempotency_key="8002:1:inquiry:1",
                     status="pending", instance_id="DT-TAKEN")
    oa_id = make_row(db)
    fake.payloads["DT-TAKEN"] = dict(ADOPT_PAYLOAD)
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                resolve(SessionAsAsync(raw), oa_id, action="adopt", instance_id="DT-TAKEN")
            )
    assert caught.value.code == ErrorCode.DUPLICATE
    assert "8002" in caught.value.message
    assert reload_row(db, oa_id).instance_id is None
    assert reload_row(db, taken).instance_id == "DT-TAKEN"


def test_adopt_requires_instance_id(db, fake):
    oa_id = make_row(db)
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(resolve(SessionAsAsync(raw), oa_id, action="adopt"))
    assert caught.value.code == ErrorCode.REQUIRED_FIELD_MISSING


@pytest.mark.parametrize("blank", ["   ", "\t", " \n "])
def test_adopt_rejects_blank_instance_id_before_any_external_call(db, fake, blank):
    """**空白串不算填了单号**：必须先 strip 再判空。

    修复前这里是 `if not instance_id` 排在 `.strip()` 前面，于是"三个空格"
    会被当成有效单号直接拿去钉钉查，用户看到的是 502「无法核实审批单 ''」——
    明明是他没填，却被报成外部故障，还会白打一次真实钉钉查询。
    """
    oa_id = make_row(db)
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                resolve(SessionAsAsync(raw), oa_id, action="adopt", instance_id=blank)
            )
    assert caught.value.code == ErrorCode.REQUIRED_FIELD_MISSING
    assert caught.value.http_status == 422
    assert fake.get_calls == 0, "空白单号绝不能拿去查外部"
    after = reload_row(db, oa_id)
    assert after.status == "needs_review"
    assert after.resolve_state == "idle"


# --------------------------------------------------------------------------
# 7.8 同请求键回放 / 不同并发核定冲突 / 僵死占用不盲目再建
# --------------------------------------------------------------------------


def test_same_request_key_replays_same_result(db, fake):
    """同一个请求键重放：回放同一份结果，**不再向钉钉发起**。"""
    oa_id = make_row(db)
    with Session(db, expire_on_commit=False) as raw:
        s = SessionAsAsync(raw)
        first = asyncio.run(
            resolve(s, oa_id, action="resend", request_key="K-REPLAY")
        )
        second = asyncio.run(resolve(s, oa_id, action="resend", request_key="K-REPLAY"))
    assert fake.calls == 1, "同一次核定重放不能变成第二次外部创建"
    assert second.instance_id == first.instance_id
    assert second.status == "pending"


def test_stale_claim_takeover_refuses_to_blindly_resend(db, fake):
    """接管僵死占用后**不盲目再建**：先让人到钉钉核实（7.8 明确要求）。"""
    oa_id = make_row(
        db,
        resolve_state="processing",
        resolve_request_key="K-DEAD",
        resolve_claimed_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                resolve(SessionAsAsync(raw), oa_id, action="resend", request_key="K-NEW")
            )
    assert caught.value.code == ErrorCode.VERSION_CONFLICT
    assert "先到钉钉" in caught.value.message
    assert fake.calls == 0, "接管僵死占用不能直接再建一张"
    after = reload_row(db, oa_id)
    assert after.status == "needs_review", "结果仍然未知，不能假装失败"
    assert after.resolve_state == "idle", "占用要放掉，否则人永远核不了"


def test_stale_claim_takeover_allows_verified_adopt(db, fake):
    """僵死占用下"认领"是允许的：它向钉钉核实过来源，不是盲目再建。"""
    make_inquiry(db)
    oa_id = make_row(
        db,
        resolve_state="processing",
        resolve_request_key="K-DEAD",
        resolve_claimed_at=datetime.now(UTC) - timedelta(minutes=30),
    )
    fake.payloads["DT-LATE"] = dict(ADOPT_PAYLOAD)
    with Session(db, expire_on_commit=False) as raw:
        row = asyncio.run(
            resolve(SessionAsAsync(raw), oa_id, action="adopt", instance_id="DT-LATE")
        )
    assert row.instance_id == "DT-LATE"
    assert fake.calls == 0, "认领不该再建单"


def test_fresh_claim_blocks_second_resolve(db, fake):
    """有人正在核定（占用未僵死）：第二个请求必须明确冲突，而不是各建一张。"""
    oa_id = make_row(
        db,
        resolve_state="processing",
        resolve_request_key="K-RUNNING",
        resolve_claimed_at=datetime.now(UTC),
    )
    with Session(db, expire_on_commit=False) as raw:
        with pytest.raises(AppError) as caught:
            asyncio.run(
                resolve(SessionAsAsync(raw), oa_id, action="resend", request_key="K-OTHER")
            )
    assert caught.value.code == ErrorCode.VERSION_CONFLICT
    assert fake.calls == 0


@pytest.mark.anyio
async def test_claim_uses_fresh_row_state_not_identity_map(db, fake):
    """占用判断必须用**数据库里的新值**，而不是会话身份映射里的旧对象。

    真 PostgreSQL 上复现过（本文件第一版就是这么挂的）：第二个连接先读过
    `needs_review`（对象进了 identity map），对方提交占用之后，
    `SELECT ... FOR UPDATE` 取回来的对象**仍带着旧的 idle**——SQLAlchemy 默认
    不会用新读到的行覆盖已在身份映射里的对象。于是两个连接都"占住"了，
    各建一张外部审批单。所以这条单独用一个连接先读、另一个连接后占来守。
    """
    oa_id = make_row(db)
    s1, s2 = open_session(db), open_session(db)
    # 让第二个会话先把这一行读进身份映射（模拟"两个请求都先读了状态"）
    stale = await s2.get(OaInstance, oa_id)
    assert stale.resolve_state == "idle"

    first, was_stale = await svc.claim_external_call(s1, oa_id=oa_id, claim_key="K-1")
    assert first is not None and was_stale is False

    second, _ = await svc.claim_external_call(s2, oa_id=oa_id, claim_key="K-2")
    assert second is None, "数据库已经把它标成处理中，第二个连接不能再占住"


@pytest.mark.anyio
async def test_router_same_request_key_replays_after_success(db, fake):
    """接口层：resend 成功之后再拿**同一把请求键**调一次，必须回放原结果。

    修复前状态门排在回放判定之前，第二次会被挡成 422「当前是「审批中」」——
    外部调用确实只有一次（没有重复建单），但"同键同结果"这条约定失效了：
    弱网下客户端重发一次，用户看到的是"状态不允许"，会以为操作失败并去点别的按钮。
    """
    from app.modules.dingtalk import router as dt_router

    make_inquiry(db)
    oa_id = make_row(db)

    async def call(session, key):
        return await dt_router.resolve_oa_instance(
            oa_id=oa_id,
            payload=dt_router.ResolveOa(action="resend", request_key=key),
            request=fake_http_request(),
            user=make_user(101, permissions=("quote:manage",), data_scope="all"),
            session=session,
        )

    with Session(db, expire_on_commit=False) as raw:
        s = SessionAsAsync(raw)
        first = await call(s, "K-REPLAY-API")
    assert first["data"]["status"] == "pending"
    assert fake.calls == 1

    with Session(db, expire_on_commit=False) as raw:
        s = SessionAsAsync(raw)
        second = await call(s, "K-REPLAY-API")

    assert fake.calls == 1, "同键重放不能变成第二次外部创建"
    assert second["data"]["instance_id"] == first["data"]["instance_id"]
    assert "重放" in (second["message"] or ""), second["message"]


@pytest.mark.anyio
async def test_two_concurrent_resolves_create_only_one_instance(db, fake):
    """7.8 的命门：两条连接同时核定，**只有一个**能发起外部创建。

    修复前两个请求都能读到 `needs_review`、各自通过检查，于是各建一张外部单。
    现在互斥落在 `oa_instances` 的行级原子占用上：第二个请求拿到明确的 409。
    """

    class BarrierClient(FakeClient):
        async def create_process_instance(self, **kwargs):  # noqa: ARG002
            self.calls += 1
            try:
                # 屏障：把"调外部"这一段撑开，让对手有时间读完同一行
                await asyncio.wait_for(self.barrier.wait(), timeout=0.4)
            except (asyncio.TimeoutError, TimeoutError):
                pass  # 修复后只有一个人到得了这里，屏障自然等不到第二个人
            return f"FAKE-{self.calls}"

    client = BarrierClient()
    client.barrier = asyncio.Barrier(2)
    # 本用例的假客户端带屏障，不能用 fake 夹具那个：直接替换模块级名字，
    # monkeypatch 在用例结束时把它复原
    original = svc.get_client
    svc.get_client = lambda: client
    try:
        oa_id = make_row(db)
        sessions = [open_session(db), open_session(db)]

        async def one(session, key):
            row = await session.get(OaInstance, oa_id)
            # 两个请求几乎同时到达：先让对手也把"结果未知"读出来，把竞态窗口撑开
            await asyncio.sleep(0)
            try:
                return await svc.resolve_reviewed_instance(
                    session, row, action="resend", request_key=key
                )
            except AppError as exc:
                return exc

        results = await asyncio.gather(one(sessions[0], "K-A"), one(sessions[1], "K-B"))
    finally:
        svc.get_client = original

    assert client.calls == 1, "并发核定只能有一次外部创建"
    conflicts = [r for r in results if isinstance(r, AppError)]
    assert len(conflicts) == 1, "另一个请求必须拿到明确冲突"
    assert conflicts[0].code == ErrorCode.VERSION_CONFLICT
    assert reload_row(db, oa_id).status == "pending"
