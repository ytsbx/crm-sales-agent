"""ERP / MES Adapter（05-TECH §15）。

分层意图与企微那套一致：**业务代码不碰对方系统的字段结构**。
`service.py` 只说"把这个订单推过去、把那个订单的状态拉回来"，
具体是聚水潭、ERP321 还是别的系统，全在 Adapter 里。

CRM 侧口径（订单状态）固定为 ORDER_STATUS_LABEL 那六个值，
Adapter 负责把对方的状态词翻译过来。这样换 ERP 时业务与前端都不用动。

幂等（02-ER §21「SalesOrder 转 ERP/MES 必须幂等」）：
- 推送前先查 `external_mappings` / `sales_orders.erp_order_id`，
  已经有外部单号就直接返回，不重复建单；
- 请求体带 `idempotency_key`（用 CRM 订单号，稳定可复现），
  对方系统据此去重。两者叠加，重试不会产生两张单。

关于"还没拿到凭据"：与企微一致，缺配置抛 `ErpNotConfigured`，
接口层翻成 50203 并说明缺哪个变量，**不假装推送成功**。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.core.config import settings


class ErpError(RuntimeError):
    """对方系统返回了业务错误。"""

    def __init__(self, message: str, *, api: str, code: str | None = None) -> None:
        self.api = api
        self.code = code
        super().__init__(f"ERP 接口 {api} 返回错误：{message}")


class ErpNotConfigured(RuntimeError):
    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(f"ERP/MES 还没配置：缺少 {missing}，请先在后端 .env 里补齐")


# CRM 订单状态 ← 对方系统状态词。各 ERP 的叫法不同，映射表放在各自 Adapter 里。
CRM_STATUS = ("pending", "in_production", "shipped", "delivered", "completed", "cancelled")


class ErpAdapter(ABC):
    """ERP/MES 适配层接口。新增一家 ERP 只需实现这个类并注册到 PROVIDERS。"""

    #: 用于 external_mappings.system_type 与集成日志
    system_type: str = "ERP"
    label: str = "ERP/MES"

    @abstractmethod
    def readiness(self) -> tuple[bool, str]:
        """返回 (是否已配置, 缺什么)。"""

    @abstractmethod
    async def push_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """推送销售订单，返回 {external_id, external_code, raw}。"""

    @abstractmethod
    async def fetch_order_status(self, external_id: str) -> dict[str, Any]:
        """拉履约状态，返回 {status(CRM 口径), shipped_at, raw}。"""

    async def aclose(self) -> None:  # pragma: no cover - 默认无资源可释放
        return None


class StubAdapter(ErpAdapter):
    """未配置时的占位 Adapter：任何调用都明确报错。

    刻意不返回"已推送"或空状态：那会让订单详情页显示成已同步，
    而 ERP 里其实什么都没有 —— 这是最难排查的一类问题。
    """

    def __init__(self, system_type: str = "ERP", label: str = "ERP/MES") -> None:
        self.system_type = system_type
        self.label = label

    def readiness(self) -> tuple[bool, str]:
        missing = []
        if not settings.erp_base_url:
            missing.append("ERP_BASE_URL")
        if not settings.erp_app_key:
            missing.append("ERP_APP_KEY")
        if not settings.erp_app_secret:
            missing.append("ERP_APP_SECRET")
        return (False, "、".join(missing) or "ERP_PROVIDER")

    async def push_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        _, missing = self.readiness()
        raise ErpNotConfigured(missing)

    async def fetch_order_status(self, external_id: str) -> dict[str, Any]:
        _, missing = self.readiness()
        raise ErpNotConfigured(missing)


class JushuitanAdapter(ErpAdapter):
    """聚水潭开放平台。

    公司服务器上已有 `erp-bridge`（聚水潭 + ERP321）对接服务，
    拿到它的调用方式后优先复用；这里按键值直连的形态实现，
    字段名按聚水潭开放平台的命名（wdt 前缀）。

    注意：聚水潭是否开放**建单**权限还没确认（08-待领导确认清单 §2-3），
    没开权限时 `push_order` 会返回业务错误，接口层会原样透出，不会静默忽略。
    """

    system_type = "ERP"
    label = "聚水潭"

    # 聚水潭订单状态 → CRM 口径。
    # 未列出的状态一律不猜，service 会保持原状态并记一条日志。
    STATUS_MAP = {
        "WaitConfirm": "pending",
        "WaitPay": "pending",
        "Confirmed": "in_production",
        "WaitProduce": "pending",
        "Producing": "in_production",
        "WaitDeliver": "in_production",
        "Delivering": "shipped",
        "Sent": "shipped",
        "WaitConfirmReceive": "shipped",
        "Received": "delivered",
        "Finished": "completed",
        "Cancelled": "cancelled",
        "Void": "cancelled",
    }

    def __init__(self, http: httpx.AsyncClient | None = None) -> None:
        self._http = http

    def readiness(self) -> tuple[bool, str]:
        missing = []
        if not settings.erp_base_url:
            missing.append("ERP_BASE_URL")
        if not settings.erp_app_key:
            missing.append("ERP_APP_KEY")
        if not settings.erp_app_secret:
            missing.append("ERP_APP_SECRET")
        if missing:
            return False, "、".join(missing)
        return True, ""

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=settings.erp_base_url, timeout=30.0)
        return self._http

    async def _call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        ready, missing = self.readiness()
        if not ready:
            raise ErpNotConfigured(missing)
        client = await self._client()
        # 聚水潭用 app_key + timestamp + sign 鉴权；sign 的算法按对方文档实现，
        # 目前没有真实密钥无法验证，所以这里先只带 app_key，
        # 拿到凭据后按 erp-bridge 里的实现补 sign（见 README 的说明）。
        payload = {
            **body,
            "app_key": settings.erp_app_key,
        }
        response = await client.post(path, json=payload)
        response.raise_for_status()
        data = response.json()
        # 聚水潭约定 code=0 为成功
        if int(data.get("code", 0)) != 0:
            raise ErpError(
                str(data.get("msg") or data), api=path, code=str(data.get("code"))
            )
        return data

    async def push_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._call("/open/orders/upload", payload)
        result = data.get("data") or {}
        first = (result.get("items") or [{}])[0] if isinstance(result.get("items"), list) else {}
        return {
            "external_id": str(first.get("o_id") or result.get("o_id") or ""),
            "external_code": str(first.get("so_id") or payload.get("so_id") or ""),
            "raw": data,
        }

    async def fetch_order_status(self, external_id: str) -> dict[str, Any]:
        data = await self._call("/open/orders/query", {"o_id": external_id})
        orders = ((data.get("data") or {}).get("orders")) or []
        if not orders:
            return {"status": None, "shipped_at": None, "raw": data}
        first = orders[0]
        raw_status = str(first.get("status") or "")
        return {
            "status": self.STATUS_MAP.get(raw_status),
            "raw_status": raw_status,
            "shipped_at": first.get("send_date"),
            "raw": data,
        }

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None


#: 可选的适配器。换 ERP 只改 .env 里的 ERP_PROVIDER。
PROVIDERS: dict[str, type[ErpAdapter]] = {
    "jushuitan": JushuitanAdapter,
    "jst": JushuitanAdapter,
}

_INSTANCES: dict[str, ErpAdapter] = {}


def get_adapter(provider: str | None = None) -> ErpAdapter:
    """按配置取 Adapter 单例；没配或不认识就返回 Stub（调用即报错）。"""
    name = (provider or settings.erp_provider or "").strip().lower()
    if not name:
        return StubAdapter()
    if name not in PROVIDERS:
        return StubAdapter(label=f"未知 ERP：{name}")
    if name not in _INSTANCES:
        _INSTANCES[name] = PROVIDERS[name]()
    return _INSTANCES[name]


async def reset_adapters() -> None:
    """关闭已建连接（应用退出时调用）。"""
    for adapter in _INSTANCES.values():
        await adapter.aclose()
    _INSTANCES.clear()
