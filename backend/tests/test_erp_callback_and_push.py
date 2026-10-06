"""ERP 回调与推单（第八批 §8.11）：双编号同源、事件幂等、外部受理凭据、结果未知。

这些用例守的四个缺陷（都是交接说明 §8.11 / §6 里已经复现过的）：

1. **双编号不同源也照改状态**：回调同时给 `order_no` 和 `erp_order_id` 时只按前者定位，
   不核对后者。复现：CRM-A 已绑 ERP-A，送进 ERP-B 的事件仍然 `matched=True`
   并把订单状态改掉 —— 别人系统的单号能驱动我们这单的状态。
2. **未匹配/冲突事件只存编号**：`raw_status`/`remark` 等完整载荷丢掉，
   事后补映射无法重放；同事件重放还会反复写日志。
3. **没有外部受理凭据也算已同步**：适配器返回空 `external_id` 时服务层仍 `pushed=True`，
   并把**本地 CRM 单号**写进 `sales_orders.erp_order_id` —— 库里显示"已推送"，
   对方系统里其实什么都没有。
4. **结果未知没有持久化**：网络超时/响应缺字段时既没留下可查的请求状态，
   重启后也无从判断该不该重推。

不连数据库、不连网络：服务层真正发出的 select 由最小 FakeSession 回答，
外部系统一律用替身并**显式打桩**（交接说明 §1：只做不连库不连网的检查）。
真正的 PostgreSQL 并发/重启恢复在 `scripts/check_erp_push_recovery.py`。
"""

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.modules.erp import adapter as adapter_module
from app.modules.erp import service as svc
from app.modules.erp.adapter import ErpError, JushuitanAdapter
from app.modules.integration.model import ExternalMapping, IntegrationLog
from app.modules.order.model import OrderStatusHistory, SalesOrder

# ---------------------------------------------------------------- 替身


class _Rows:
    """`session.execute(...)` 的返回值：只实现服务层用到的取数方式。"""

    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar_one(self):
        return self._rows[0] if self._rows else 0


class FakeSession:
    """最小 AsyncSession 替身：按 select 的目标实体 + 绑定参数过滤。

    为什么不用 MagicMock：这里要断言的是"服务层到底查了什么、写了什么"，
    用编译后的绑定参数做派发，能同时兼容修复前后两版代码发出的不同 select。
    """

    def __init__(self, *, orders=(), mappings=(), logs=(), counts=None):
        self.orders = list(orders)
        self.mappings = list(mappings)
        self.logs = list(logs)
        self.counts = counts or {}
        self.added: list = []
        self.commits = 0
        self.flushes = 0
        self.rolled_back = 0
        self._next_id = 900

    # -- 查询 ---------------------------------------------------------------
    async def execute(self, stmt):
        entity = None
        descriptions = getattr(stmt, "column_descriptions", None) or []
        if descriptions:
            entity = descriptions[0].get("entity")
        try:
            params = stmt.compile().params
        except Exception:  # pragma: no cover - 兜底，正常语句都能编译
            params = {}
        if entity is SalesOrder:
            return _Rows(_filter(self.orders, params, ("order_no", "erp_order_id", "id")))
        if entity is ExternalMapping:
            return _Rows(
                _filter(
                    self.mappings,
                    params,
                    ("system_type", "business_type", "internal_id", "external_id"),
                )
            )
        if entity is IntegrationLog:
            return _Rows(
                _filter(
                    self.logs,
                    params,
                    ("integration_type", "direction", "business_type", "business_id", "status"),
                )
            )
        # func.count() 之类的聚合：返回计划好的数字（按实体名取）
        return _Rows([self.counts.get(getattr(entity, "__name__", ""), 0)])

    async def get(self, entity, pk):
        pool = {
            SalesOrder: self.orders,
            ExternalMapping: self.mappings,
            IntegrationLog: self.logs,
        }.get(entity, [])
        return next((row for row in pool if getattr(row, "id", None) == pk), None)

    # -- 写入 ---------------------------------------------------------------
    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, IntegrationLog):
            self.logs.append(obj)
        elif isinstance(obj, ExternalMapping):
            self.mappings.append(obj)

    async def flush(self):
        self.flushes += 1
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rolled_back += 1


def _filter(rows, params, fields):
    for field in fields:
        for key in (f"{field}_1", field):
            if key in params:
                want = params[key]
                rows = [row for row in rows if getattr(row, field, None) == want]
                break
    return rows


class FakeAdapter:
    """ERP 适配器替身。记录每一次推送报文，绝不发真实请求。"""

    system_type = "ERP"
    label = "测试ERP"
    STATUS_MAP = {"Confirmed": "in_production", "Sent": "shipped", "Cancelled": "cancelled"}

    def __init__(self, *, result=None, error=None):
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def push_order(self, payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return self.result

    async def fetch_order_status(self, external_id):  # pragma: no cover - 本文件未用到
        return {"status": None, "raw": {}}

    def missing_config(self):
        return []

    def capabilities(self):
        return {}


def _order(**kwargs):
    base = dict(
        id=1,
        order_no="CRM-A",
        erp_order_id=None,
        status="pending",
        owner_id=1,
        customer_id=1,
        total_amount=Decimal("100"),
        currency="CNY",
        remark=None,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        delivery_date=None,
        payment_terms=None,
        cancelled_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _mapping(**kwargs):
    base = dict(
        id=11,
        system_type="ERP",
        business_type="order",
        internal_id=1,
        external_id=None,
        external_code=None,
        last_sync_at=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _patch_adapter(monkeypatch, adapter):
    monkeypatch.setattr(svc, "get_adapter", lambda *a, **k: adapter)


def _patch_async(monkeypatch, name, value):
    """打桩一个可能是"修复后才存在"的辅助函数（用协程返回值）。"""

    async def _stub(*args, **kwargs):
        return value

    monkeypatch.setattr(svc, name, _stub, raising=False)


def _patch_sync(monkeypatch, name, value):
    monkeypatch.setattr(svc, name, lambda *a, **k: value, raising=False)


def _patch_side_session_log(monkeypatch, session, *, adapter, order, log_id):
    """打桩"独立会话落请求记录"。

    真实实现用一个独立会话把 sending 记录写进库（这样它不受本次请求回滚影响）。
    这里把同一条记录放进 FakeSession，好让 push_order 后面按 id 读回它。
    """

    def _persist(**kwargs):
        row = IntegrationLog(
            id=log_id,
            integration_type="erp",
            provider=adapter.label,
            direction="outbound",
            business_type="order",
            business_id=order.id,
            request_data=kwargs.get("payload"),
            status="sending",
            response_data={"processing_status": "sending", "order_no": order.order_no},
        )
        session.logs.append(row)
        return _await(log_id)

    monkeypatch.setattr(svc, "_persist_push_request", _persist, raising=False)


# ---------------------------------------------------------------- §8.11 回调


def test_dual_number_mismatch_must_not_touch_any_order(monkeypatch):
    """CRM-A 已绑 ERP-A，回调却给 ERP-B：两个编号不同源，必须拒绝改状态。

    修复前：只按 order_no 定位到 CRM-A，ERP-B 完全不核对 → matched=True，
    订单被改成 in_production（复现交接说明 §6 的那条证据）。
    """
    crm_a = _order(id=1, order_no="CRM-A", erp_order_id="ERP-A")
    _patch_adapter(monkeypatch, FakeAdapter())
    _patch_async(monkeypatch, "_find_processed_event", None)
    _patch_async(monkeypatch, "_find_order_mapping", None)
    session = FakeSession(orders=[crm_a])

    result = asyncio.run(
        svc.apply_status_webhook(
            session, order_no="CRM-A", erp_order_id="ERP-B", raw_status="Confirmed"
        )
    )

    assert result["matched"] is False, "双编号不同源不得判定为已匹配"
    assert result.get("conflict") is True, "必须明确回报这是编号冲突"
    assert result["changed"] is False
    assert crm_a.status == "pending", "冲突时不得修改任何一个订单"
    assert [row for row in session.added if isinstance(row, OrderStatusHistory)] == []
    # 冲突要进异常队列（可模拟的承载）：完整事件 + 冲突原因都要留得下
    conflicts = [row for row in session.logs if row.status == "conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].request_data["status"] == "Confirmed"
    assert "ERP-B" in str(conflicts[0].response_data)


def test_dual_number_same_source_still_applies(monkeypatch):
    """护栏：两个编号确实同源时必须照常改状态，不能为了修冲突把正常路径堵死。"""
    crm_a = _order(id=1, order_no="CRM-A", erp_order_id="ERP-A")
    _patch_adapter(monkeypatch, FakeAdapter())
    _patch_async(monkeypatch, "_find_processed_event", None)
    session = FakeSession(orders=[crm_a])

    result = asyncio.run(
        svc.apply_status_webhook(
            session, order_no="CRM-A", erp_order_id="ERP-A", raw_status="Confirmed"
        )
    )

    assert result["matched"] is True
    assert result["changed"] is True
    assert crm_a.status == "in_production"
    assert len([row for row in session.added if isinstance(row, OrderStatusHistory)]) == 1


def test_unmatched_event_keeps_the_whole_event_and_a_stable_key(monkeypatch):
    """未匹配事件要留下**可信完整事件**（含 raw_status/remark）与稳定事件键。

    修复前：只写 {"order_no": ..., "erp_order_id": ...}，状态是 failed ——
    事后补了映射也没法知道当时推的是什么状态，等于无法重放。
    """
    _patch_adapter(monkeypatch, FakeAdapter())
    _patch_async(monkeypatch, "_find_processed_event", None)
    session = FakeSession(orders=[])

    result = asyncio.run(
        svc.apply_status_webhook(
            session, order_no=None, erp_order_id="ERP-X", raw_status="Sent", remark="顺丰 SF123"
        )
    )

    assert result["matched"] is False
    assert result.get("unmatched") is True
    log = session.logs[-1]
    assert log.status == "unmatched"
    event = log.request_data
    assert event["status"] == "Sent" and event["remark"] == "顺丰 SF123"
    assert event["erp_order_id"] == "ERP-X"
    assert event["event_key"] == svc.build_status_event_key(
        order_no=None, erp_order_id="ERP-X", raw_status="Sent", remark="顺丰 SF123"
    )
    assert log.response_data["processing_status"] == "unmatched"
    assert log.response_data["event_key"] == event["event_key"]


def test_event_key_is_stable_and_separates_different_events():
    """稳定事件键：同一事件每次算出来一样；合法不同事件必须分开。"""
    key = svc.build_status_event_key(
        order_no="CRM-A", erp_order_id=None, raw_status="Sent", remark="发货"
    )
    same = svc.build_status_event_key(
        order_no="CRM-A", erp_order_id=None, raw_status="Sent", remark="发货"
    )
    other_status = svc.build_status_event_key(
        order_no="CRM-A", erp_order_id=None, raw_status="Confirmed", remark="发货"
    )
    other_remark = svc.build_status_event_key(
        order_no="CRM-A", erp_order_id=None, raw_status="Sent", remark="取消发货"
    )
    other_order = svc.build_status_event_key(
        order_no="CRM-B", erp_order_id=None, raw_status="Sent", remark="发货"
    )
    assert key == same
    assert len({key, other_status, other_remark, other_order}) == 4


def test_replayed_event_is_not_written_again(monkeypatch):
    """同事件重放：不再写历史/日志，直接回报"已处理过"（可解释）。"""
    key = svc.build_status_event_key(
        order_no="CRM-A", erp_order_id=None, raw_status="Sent", remark=None
    )
    prior = SimpleNamespace(
        id=77,
        status="success",
        business_id=1,
        response_data={
            "event_key": key,
            "processing_status": "processed",
            "matched": True,
            "changed": True,
        },
    )
    _patch_adapter(monkeypatch, FakeAdapter())
    _patch_async(monkeypatch, "_find_processed_event", prior)
    session = FakeSession(orders=[_order(id=1, order_no="CRM-A")], logs=[prior])

    result = asyncio.run(
        svc.apply_status_webhook(session, order_no="CRM-A", erp_order_id=None, raw_status="Sent")
    )

    assert result["replayed"] is True
    assert result["matched"] is True
    assert session.added == [], "重放不得再写日志或状态历史"
    assert session.logs == [prior]


def test_unmatched_event_can_be_recovered_after_mapping_is_added(monkeypatch):
    """先未匹配、后补映射：同一事件重放必须能恢复处理（不能因去重被永久挡掉）。"""
    crm_a = _order(id=1, order_no="CRM-A", erp_order_id=None)
    mapping = _mapping(id=11, internal_id=1, external_id="ERP-X")
    _patch_adapter(monkeypatch, FakeAdapter())
    _patch_async(monkeypatch, "_find_processed_event", None)  # 未匹配的事件不算"已处理"
    session = FakeSession(orders=[crm_a], mappings=[mapping])

    result = asyncio.run(
        svc.apply_status_webhook(session, order_no=None, erp_order_id="ERP-X", raw_status="Sent")
    )

    assert result["matched"] is True and result["changed"] is True
    assert crm_a.status == "shipped"


def test_event_key_ignores_sorting_of_payload_fields(monkeypatch):
    """事件键只认业务字段：同一事件的 JSON 字段顺序不同不能算两个事件。"""
    first = svc.build_status_event_key(
        order_no="CRM-A", erp_order_id="ERP-A", raw_status="Sent", remark="x"
    )
    second = svc.build_status_event_key(
        remark="x", raw_status="Sent", erp_order_id="ERP-A", order_no="CRM-A"
    )
    assert first == second


# ---------------------------------------------------------------- §8.11 推单


def test_push_without_external_credential_is_not_marked_synced(monkeypatch):
    """适配器没给出外部单号：**不能**标已同步，更不能把本地单号当外部单号。

    修复前：external_code 回退成本地 so_id，order.erp_order_id 被写成 "SO-A"，
    函数返回 pushed=True —— 库里显示已推送，对方系统里什么都没有。
    """
    order = _order(id=1, order_no="SO-A")
    adapter = FakeAdapter(result={"external_id": "", "external_code": "SO-A", "raw": {"code": 0}})
    _patch_adapter(monkeypatch, adapter)
    _patch_async(monkeypatch, "_lock_order", None)  # 修复前没有这个辅助函数

    async def _payload(session, order):
        return {"so_id": "SO-A", "idempotency_key": "SO-A"}

    monkeypatch.setattr(svc, "build_order_payload", _payload, raising=False)
    session = FakeSession(orders=[order])
    _patch_side_session_log(monkeypatch, session, adapter=adapter, order=order, log_id=501)

    with pytest.raises(ErpError) as caught:
        asyncio.run(svc.push_order(session, order=order, operator_id=1))

    assert caught.value.kind == "result_unknown"
    assert order.erp_order_id is None, "没有外部受理凭据不得写外部单号"
    assert session.mappings == [], "没有可信凭据不得建映射"
    assert [row for row in session.logs if row.status == "unknown"], "结果未知必须留下可查的请求状态"
    assert [row for row in session.added if isinstance(row, OrderStatusHistory)] == []


def test_push_success_records_external_credential(monkeypatch):
    """护栏：拿到真实外部单号时正常落库（映射 + 主表 + 状态历史）。"""
    order = _order(id=1, order_no="SO-A")
    adapter = FakeAdapter(
        result={"external_id": "ERP-9", "external_code": "SO-A", "raw": {"code": 0}}
    )
    _patch_adapter(monkeypatch, adapter)
    _patch_async(monkeypatch, "_lock_order", None)

    async def _payload(session, order):
        return {"so_id": "SO-A", "idempotency_key": "SO-A"}

    monkeypatch.setattr(svc, "build_order_payload", _payload, raising=False)
    session = FakeSession(orders=[order])
    _patch_side_session_log(monkeypatch, session, adapter=adapter, order=order, log_id=502)

    result = asyncio.run(svc.push_order(session, order=order, operator_id=1))

    assert result["pushed"] is True and result["already_synced"] is False
    assert order.erp_order_id == "ERP-9"
    assert session.mappings and session.mappings[0].external_id == "ERP-9"
    assert [row.status for row in session.logs if row.status == "success"]
    assert len([row for row in session.added if isinstance(row, OrderStatusHistory)]) == 1


def test_existing_mapping_blocks_second_push_after_restart(monkeypatch):
    """已有映射、主表为空（上次只落了一半 / 进程重启）：先补主表，**不再建单**。

    修复前：只在 order.erp_order_id 非空时才提前返回，这种情况会真的再推一次 ——
    对方系统里出现第二张单。
    """
    order = _order(id=1, order_no="SO-A", erp_order_id=None)
    mapping = _mapping(id=11, internal_id=1, external_id="ERP-9")
    adapter = FakeAdapter(result={"external_id": "ERP-9"})
    _patch_adapter(monkeypatch, adapter)
    _patch_async(monkeypatch, "_lock_order", None)
    session = FakeSession(orders=[order], mappings=[mapping])

    result = asyncio.run(svc.push_order(session, order=order, operator_id=1))

    assert adapter.calls == [], "已建过外单就不能再调一次推送"
    assert result["already_synced"] is True and result.get("recovered") is True
    assert order.erp_order_id == "ERP-9", "主表要按已有映射补齐"


def test_inflight_unknown_push_blocks_retry(monkeypatch):
    """上次结果未知（sending/unknown）：先核对再重试，不得盲目重推。"""
    order = _order(id=1, order_no="SO-A", erp_order_id=None)
    marker = SimpleNamespace(id=61, status="sending", response_data={"processing_status": "sending"})
    adapter = FakeAdapter(result={"external_id": "ERP-9"})
    _patch_adapter(monkeypatch, adapter)
    _patch_async(monkeypatch, "_lock_order", None)
    _patch_async(monkeypatch, "_find_inflight_push", marker)
    session = FakeSession(orders=[order], logs=[marker])

    with pytest.raises(ErpError) as caught:
        asyncio.run(svc.push_order(session, order=order, operator_id=1))

    assert caught.value.kind == "result_unknown"
    assert adapter.calls == []
    assert order.erp_order_id is None


def test_mapping_and_main_table_conflict_is_refused(monkeypatch):
    """映射与主表外部单号互相矛盾：拒绝再推（否则可能造出第三张外部单）。"""
    order = _order(id=1, order_no="SO-A", erp_order_id="ERP-A")
    mapping = _mapping(id=11, internal_id=1, external_id="ERP-B")
    adapter = FakeAdapter(result={"external_id": "ERP-C"})
    _patch_adapter(monkeypatch, adapter)
    _patch_async(monkeypatch, "_lock_order", None)
    session = FakeSession(orders=[order], mappings=[mapping])

    with pytest.raises(ErpError) as caught:
        asyncio.run(svc.push_order(session, order=order, operator_id=1))

    assert caught.value.kind == "mapping_mismatch"
    assert adapter.calls == []
    assert order.erp_order_id == "ERP-A"


def _await(value):
    async def _coro():
        return value

    return _coro()


# ---------------------------------------------------------------- §8.12 适配器


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeHttp:
    def __init__(self, data):
        self._data = data
        self.posts: list = []

    async def post(self, path, json=None):
        self.posts.append((path, json))
        return _FakeResponse(self._data)

    async def aclose(self):
        return None


def _configure(monkeypatch):
    monkeypatch.setattr(adapter_module.settings, "erp_base_url", "https://example.invalid")
    monkeypatch.setattr(adapter_module.settings, "erp_app_key", "key")
    monkeypatch.setattr(adapter_module.settings, "erp_app_secret", "secret")


def _verify(monkeypatch, capability):
    monkeypatch.setattr(
        JushuitanAdapter,
        f"{capability.upper()}_CAPABILITY",
        adapter_module.ErpCapability(
            key=capability, label=capability, verified=True, evidence="单元测试替身"
        ),
    )


def test_missing_code_in_response_is_not_success(monkeypatch):
    """响应没有 code 字段：不能按默认 0 当成成功（原来就是这么错的）。"""
    _configure(monkeypatch)
    _verify(monkeypatch, "read")
    http = _FakeHttp({"data": {}})
    adapter = JushuitanAdapter(http=http)

    with pytest.raises(ErpError) as caught:
        asyncio.run(adapter._call("/open/orders/query", {"o_id": "1"}, capability="read"))

    assert "code" in str(caught.value)


def test_peer_error_code_is_surfaced(monkeypatch):
    """对方返回非 0 业务码：原样带出 code 与 msg，便于定位。"""
    _configure(monkeypatch)
    _verify(monkeypatch, "read")
    http = _FakeHttp({"code": 1005, "msg": "sign error"})
    adapter = JushuitanAdapter(http=http)

    with pytest.raises(ErpError) as caught:
        asyncio.run(adapter._call("/open/orders/query", {"o_id": "1"}, capability="read"))

    assert caught.value.code == "1005"
    assert "sign error" in str(caught.value)


def test_unverified_write_capability_never_calls_out(monkeypatch):
    """写入未验收就**不许发请求**，也不许返回假成功（§8.12）。"""
    _configure(monkeypatch)
    http = _FakeHttp({"code": 0, "data": {"items": [{"o_id": "ERP-1"}]}})
    adapter = JushuitanAdapter(http=http)

    with pytest.raises(ErpError) as caught:
        asyncio.run(adapter.push_order({"so_id": "SO-A"}))

    assert caught.value.kind == "not_verified"
    assert http.posts == []


def test_readiness_states_are_distinguished():
    """五态状态机：未配置 / 已配置未验证 / 只读已验收 / 写入已验收 / 故障。"""
    unverified = {
        "read": adapter_module.ErpCapability(key="read", label="只读", verified=False),
        "write": adapter_module.ErpCapability(key="write", label="写入", verified=False),
    }
    read_ok = {
        **unverified,
        "read": adapter_module.ErpCapability(key="read", label="只读", verified=True),
    }
    write_ok = {
        **read_ok,
        "write": adapter_module.ErpCapability(key="write", label="写入", verified=True),
    }
    state = svc.connection_state
    assert state(configured=False, capabilities=unverified, latest_failure=None) == "not_configured"
    assert (
        state(configured=True, capabilities=unverified, latest_failure=None)
        == "configured_unverified"
    )
    assert state(configured=True, capabilities=read_ok, latest_failure=None) == "readonly_verified"
    assert state(configured=True, capabilities=write_ok, latest_failure=None) == "write_verified"
    assert (
        state(
            configured=True,
            capabilities=write_ok,
            latest_failure={"error_class": "signature", "message": "sign error"},
        )
        == "fault"
    )


def test_auth_error_classes_are_separable(monkeypatch):
    """错误签名 / 过期凭据 / 无店铺权限要能给出不同结论。

    真实码表要靠官方/桥接资料（§0.3 第 5 条），所以这里验证的是**机制**：
    码表填好后三类码分别归类；没填的码一律"未分类"并保留原始码值，
    绝不凭记忆把某个码猜成鉴权失败。
    """
    monkeypatch.setattr(
        adapter_module,
        "ERROR_CODE_CLASS",
        {
            "1005": adapter_module.ERR_CLASS_SIGNATURE,
            "1006": adapter_module.ERR_CLASS_CREDENTIAL,
            "1007": adapter_module.ERR_CLASS_SHOP_PERMISSION,
        },
    )
    classify = adapter_module.classify_erp_error
    assert classify("1005", "sign error") == adapter_module.ERR_CLASS_SIGNATURE
    assert classify("1006", "token expired") == adapter_module.ERR_CLASS_CREDENTIAL
    assert classify("1007", "no shop permission") == adapter_module.ERR_CLASS_SHOP_PERMISSION
    # 没在码表里的码：不猜
    assert classify("9999", "whatever") == adapter_module.ERR_CLASS_UNCLASSIFIED


def test_readiness_never_claims_connection_and_respects_scope(monkeypatch):
    """三个变量齐全也只到"已配置未验证"；普通查看者拿不到全公司统计与配置明细。"""
    _configure(monkeypatch)
    monkeypatch.setattr(adapter_module.settings, "erp_provider", "jushuitan")
    monkeypatch.setattr(svc, "get_adapter", lambda *a, **k: JushuitanAdapter(http=_FakeHttp({})))

    async def _scoped(session, user):
        return [1]  # 只能看自己的数据

    monkeypatch.setattr(svc, "scoped_owner_ids", _scoped, raising=False)
    session = FakeSession(counts={"SalesOrder": 3, "ExternalMapping": 2, "IntegrationLog": 5})
    viewer = SimpleNamespace(id=1, data_scope="self", roles=["sales"], has=lambda code: False)
    result = asyncio.run(svc.readiness(session, user=viewer))

    assert result["state"] == "configured_unverified"
    assert result["connected"] is False
    assert result["capabilities"]["write"]["verified"] is False
    assert "configured" not in result.get("diagnostics", {})
    assert result["scope"]["limited"] is True


def test_readiness_fault_from_systemic_auth_failure(monkeypatch):
    """最近一次真实调用是鉴权类失败 → 如实报"故障"，并带上原因。"""
    _configure(monkeypatch)
    monkeypatch.setattr(adapter_module.settings, "erp_provider", "jushuitan")
    monkeypatch.setattr(svc, "get_adapter", lambda *a, **k: JushuitanAdapter(http=_FakeHttp({})))

    async def _scoped(session, user):
        return None

    monkeypatch.setattr(svc, "scoped_owner_ids", _scoped, raising=False)
    failure = SimpleNamespace(
        id=9,
        status="failed",
        error_message="sign error",
        response_data={"error_class": adapter_module.ERR_CLASS_SIGNATURE},
    )
    session = FakeSession(counts={"SalesOrder": 1, "ExternalMapping": 0, "IntegrationLog": 1})
    _patch_async(monkeypatch, "_latest_systemic_failure", failure)
    admin = SimpleNamespace(id=1, data_scope="all", roles=["admin"], has=lambda code: True)

    result = asyncio.run(svc.readiness(session, user=admin))

    assert result["state"] == "fault"
    assert "sign error" in result["fault_reason"]
    assert result["scope"]["limited"] is False
