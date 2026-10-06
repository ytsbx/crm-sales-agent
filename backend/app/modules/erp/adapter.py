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
  对方系统据此去重。
  ⚠️ **对方是否真按这个字段去重尚未真实验收**（交接说明 §8.11），
  所以本地不能只靠报文里有个字段就认为不会重复：`service.push_order`
  自己用订单行锁保证同一张单不会被两个连接同时推出去。

关于"还没拿到凭据"：与企微一致，缺配置抛 `ErpNotConfigured`，
接口层翻成 50203 并说明缺哪个变量，**不假装推送成功**。

关于"配置齐全"（第八批 §8.12）：三个变量齐全**不等于接通**。
readiness 报的是五态（未配置 / 已配置未验证 / 只读已验收 / 写入已验收 / 故障），
读写能力各自带**验收标记**；没有验收过的能力一律在**发请求之前**拒绝，
所以"接口 ready"永远不会被当成"真实接通"。

关于只读采集（第八批 §8.13）：订单 / 发货 / 售后三类对象都要能只读拉取。
但**真实字段名、分页协议、增量参数、店铺授权都还没拿到**（交接说明 §0.3 第 5 条），
所以这里只定义**规范化记录的契约**（见 `ExternalPage`），
真实实现留给拿到官方/桥接资料的适配器；没验收的实现一律 `ErpNotVerified`，
绝不返回"假的空页"（那会让上层显示"采集成功、本期没有外部数据"，比报错更危险）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.config import settings
from app.modules.integration.vocab import COLLECT_OBJECT_TYPES

#: 接入状态五态（§8.12）。刻意不用布尔：布尔会把"配置齐全"和"真实接通"
#: 混成同一个 true，而这两件事在验收上的含义完全不同。
STATE_NOT_CONFIGURED = "not_configured"
STATE_CONFIGURED_UNVERIFIED = "configured_unverified"
STATE_READONLY_VERIFIED = "readonly_verified"
STATE_WRITE_VERIFIED = "write_verified"
STATE_FAULT = "fault"

STATE_LABELS: dict[str, str] = {
    STATE_NOT_CONFIGURED: "未配置",
    STATE_CONFIGURED_UNVERIFIED: "已配置未验证",
    STATE_READONLY_VERIFIED: "只读已验收",
    STATE_WRITE_VERIFIED: "写入已验收",
    STATE_FAULT: "故障",
}

#: 对方错误的分类。三个鉴权类分得开，是为了让"错误签名 / 过期凭据 / 无店铺权限"
#: 各自给出明确结论（§8.12 验收），而不是笼统一句"调用失败"。
ERR_CLASS_SIGNATURE = "signature"
ERR_CLASS_CREDENTIAL = "credential"
ERR_CLASS_SHOP_PERMISSION = "shop_permission"
ERR_CLASS_TRANSPORT = "transport"
ERR_CLASS_UNCLASSIFIED = "unclassified"

#: 系统性故障类：命中其中一个说明不是"这一单有问题"，而是这条通道当下不通，
#: readiness 要如实报"故障"。
SYSTEMIC_ERROR_CLASSES = (
    ERR_CLASS_SIGNATURE,
    ERR_CLASS_CREDENTIAL,
    ERR_CLASS_SHOP_PERMISSION,
)

#: 对方错误码 → 分类。**刻意留空**（交接说明 §0.3 第 5 条）：
#: 聚水潭开放平台与企业 erp-bridge 的真实错误码表还没拿到，凭记忆硬编码码表
#: 会把别的错误认成鉴权失败，**比不分类更糟**。拿到官方/桥接资料后按实际码值补。
#: 空表时一律 ERR_CLASS_UNCLASSIFIED，但原始 code 与 msg 原样保留，照样能定位。
#: 格式：{"<对方 code>": ERR_CLASS_*}
ERROR_CODE_CLASS: dict[str, str] = {}


def classify_erp_error(code: Any, message: str | None = None) -> str:
    """把对方返回的错误码归类；码表为空或没命中一律"未分类"，绝不猜。"""
    if code is not None and str(code) in ERROR_CODE_CLASS:
        return ERROR_CODE_CLASS[str(code)]
    return ERR_CLASS_UNCLASSIFIED


class ErpError(RuntimeError):
    """对方系统返回了业务错误（请求已经到达对方并被拒绝）。"""

    #: 机器可读分类。调用方（ERP 路由、订单路由）统一 catch ErpError，
    #: 再区分"明确失败"和"结果未知"用这个标记 —— 不必让每个调用点
    #: 都 import 一串新异常名。
    kind = "peer_error"

    def __init__(
        self,
        message: str,
        *,
        api: str,
        code: str | None = None,
        error_class: str | None = None,
    ) -> None:
        self.api = api
        self.code = code
        self.error_class = error_class or ERR_CLASS_UNCLASSIFIED
        super().__init__(f"ERP 接口 {api} 返回错误：{message}")


class ErpResultUnknown(ErpError):
    """请求已经发出去，但**无法确认对方是否受理/建单**。

    超时、连接中断、响应缺成功字段都属于这一类。它和"明确失败"的区别决定后续动作：
    明确失败可以直接重试；结果未知必须先核对外部单号，否则重试可能建出第二张单
    （§8.11 验收："超时重启后查到已建外单不再建"）。
    """

    kind = "result_unknown"

    def __init__(
        self,
        message: str,
        *,
        api: str = "push_order",
        code: str | None = None,
        error_class: str | None = None,
    ) -> None:
        self.api = api
        self.code = code
        self.error_class = error_class or ERR_CLASS_TRANSPORT
        RuntimeError.__init__(self, f"ERP 接口 {api} 结果未知：{message}")


class ErpNotVerified(ErpError):
    """这项对接能力还没通过真实验收 —— 拒绝发起调用，绝不返回假成功（§8.12）。"""

    kind = "not_verified"

    def __init__(self, message: str, *, api: str) -> None:
        self.api = api
        self.code = None
        self.error_class = ERR_CLASS_CREDENTIAL
        RuntimeError.__init__(self, f"ERP 接口 {api} 已拒绝调用：{message}")


class ErpMappingMismatch(ErpError):
    """本地映射与主表登记的外部单号互相矛盾：拒绝再推，避免造出第三张外部单。"""

    kind = "mapping_mismatch"

    def __init__(self, message: str, *, api: str = "push_order") -> None:
        self.api = api
        self.code = None
        self.error_class = ERR_CLASS_UNCLASSIFIED
        RuntimeError.__init__(self, f"ERP 本地映射不一致：{message}")


class ErpNotConfigured(RuntimeError):
    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(f"ERP/MES 还没配置：缺少 {missing}，请先在后端 .env 里补齐")


@dataclass(frozen=True)
class ErpCapability:
    """一项对接能力的**验收**记录。

    `verified` 只允许在真实环境验收之后改：要在 `evidence` 里写清
    谁、什么时候、在哪个环境、按哪个端点/签名算法验收的。
    **绝对不能用"三个环境变量配上了"推断它 verified** —— 那正是 §8.12 的缺陷。
    """

    key: str
    label: str
    verified: bool = False
    evidence: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "verified": self.verified,
            "enabled": self.verified,
            "evidence": self.evidence,
            "detail": self.detail,
        }


# CRM 订单状态 ← 对方系统状态词。各 ERP 的叫法不同，映射表放在各自 Adapter 里。
CRM_STATUS = ("pending", "in_production", "shipped", "delivered", "completed", "cancelled")

#: 默认能力台账：**一律未验收**。新增一家 ERP 时照抄，验收一项改一项。
UNVERIFIED_CAPABILITIES: dict[str, ErpCapability] = {
    "read": ErpCapability(
        key="read",
        label="只读查询（订单/发货状态）",
        detail="尚未按真实接口合同验收",
    ),
    "write": ErpCapability(
        key="write",
        label="写入（建单/推单）",
        detail="尚未按真实接口合同验收",
    ),
    #: §8.13 的只读采集与上面的 read 刻意分开：read 指的是"按单查状态"（推单后回读），
    #: collect 指的是"按店铺/时间窗分页拉订单、发货、售后"。两者的端点、分页协议与
    #: 店铺授权范围不同，验收进度也不会同步，合成一项就会让其中一项蒙混过关。
    "collect": ErpCapability(
        key="collect",
        label="只读采集（订单/发货/售后分页增量）",
        detail="真实字段名、分页参数与店铺授权尚未按官方资料核实",
    ),
}


@dataclass(frozen=True)
class ExternalPage:
    """只读采集的**一页规范化记录**。

    这是适配层与业务层之间唯一的契约。刻意**不含任何外部系统的字段名**：
    对方字段叫 `o_id` 还是 `orderId`，由适配器按官方资料翻译成下面这些键，
    业务层只认这一套。翻译错是适配器的事，业务层不需要、也不应该知道。

    每一条 `items` 元素的键：

      dedupe_key          必填，稳定去重键。主对象用外部编号；明细用 `父键#行键`。
      external_id         对方系统的编号（受理凭据）。主对象必填，明细可空。
      external_code       可选，**我方**业务编码（本地订单号 / SKU 编码）。
                          匹配本地对象只认它，不靠猜对方字段。
      parent_key          可选，明细行指向它所属单据的 dedupe_key。
      occurred_at         可选，外部业务发生时间（对账分期用它）。
      external_updated_at 可选，对方口径的更新时间（增量水位取最大值）。
      line_key            可选，明细行自己的行键。
      amount / currency   可选，单据金额与币种（金额对账用）。
      payload             可选，原样保留的报文，差异"点回原始证据"看的就是它。

    `next_page_token` / `has_more` 管分页：业务层每落完一页才推进断点，
    所以中断后重拉的那一页靠唯一键去重，既不漏也不重复。
    """

    items: list[dict[str, Any]] = field(default_factory=list)
    next_page_token: str | None = None
    has_more: bool = False
    #: 这一页覆盖到的最大外部更新时间（对方口径），用来推进增量水位。
    watermark: str | None = None



class ErpAdapter(ABC):
    """ERP/MES 适配层接口。新增一家 ERP 只需实现这个类并注册到 PROVIDERS。"""

    #: 用于 external_mappings.system_type 与集成日志
    system_type: str = "ERP"
    label: str = "ERP/MES"

    def capabilities_map(self) -> dict[str, ErpCapability]:
        """能力台账。基类一律未验收；每家 ERP 只覆盖自己已验收的那几项。"""
        return UNVERIFIED_CAPABILITIES

    @abstractmethod
    def missing_config(self) -> list[str]:
        """还缺哪些配置项（空列表 = 配置齐全；**不代表接通**）。"""

    @abstractmethod
    async def push_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        """推送销售订单，返回 {external_id, external_code, raw}。

        `external_id` 必须是**对方系统自己的**单号：它是"对方已受理"的唯一凭据。
        拿不到就返回空串，由 service 判成"结果未知"，不得用本地单号顶替。
        """

    @abstractmethod
    async def fetch_order_status(self, external_id: str) -> dict[str, Any]:
        """拉履约状态，返回 {status(CRM 口径), shipped_at, raw}。"""

    # ------------------------------------------------------------ 只读采集（§8.13）

    #: 一页最多取多少条。业务层会再夹一次上限，两边都夹是为了"调用方写了
    #: 一个巨大的 page_size"不至于把对方接口打挂。
    COLLECT_PAGE_SIZE_MAX = 200

    def collection_object_types(self) -> tuple[str, ...]:
        """这个适配器**承诺**支持哪些采集对象类型。

        注意"承诺支持"不等于"已经验收"：能不能真的调用由 `capability("collect")`
        决定。分开是为了让"合同上要采三类，但一类都还没验收"这个状态可表达 ——
        用户看到的就是这句实话。
        """
        return COLLECT_OBJECT_TYPES

    async def fetch_records(
        self,
        *,
        object_type: str,
        shop_id: str,
        cursor: str | None = None,
        page_size: int = 50,
    ) -> ExternalPage:
        """按对象类型只读拉一页外部事实。

        基类的实现刻意**永远拒绝**：真实端点、分页参数、增量字段、店铺授权
        都还没拿到（交接说明 §0.3 第 5 条），凭记忆写一个请求出去是拿真实数据
        当试验品。已验收的适配器覆盖这个方法即可。

        顺序也是刻意的：先报"未配置"，再报"没验收"。反过来的话，
        一个连密钥都没配的环境会显示"未验收"，运维就会去查验收记录，
        而真正该做的是先补配置。
        """
        if object_type not in self.collection_object_types():
            raise ErpError(
                f"{self.label}不支持采集对象类型 {object_type}（支持："
                f"{'、'.join(self.collection_object_types())}）",
                api=f"fetch_records:{object_type}",
            )
        missing = self.missing_config()
        if missing:
            raise ErpNotConfigured("、".join(missing))
        cap = self.capability("collect")
        raise ErpNotVerified(
            f"{self.label}的「{cap.label}」尚未通过真实环境验收：{cap.detail}。"
            "本轮只给出规范化的记录契约与水位/断点/去重机制，真实拉取列为待外部验收",
            api=f"fetch_records:{object_type}",
        )

    def supports_collection(self) -> bool:
        """能不能真的发起采集调用（只有验收过 collect 才是 True）。"""
        return bool(self.capability("collect").verified)

    def configured(self) -> bool:
        return not self.missing_config()

    def capability(self, key: str) -> ErpCapability:
        return self.capabilities_map().get(key) or ErpCapability(key=key, label=key)

    def readiness(self) -> dict[str, Any]:
        """配置与验收状态的**如实**汇总（不是"接通"的证明）。"""
        missing = self.missing_config()
        return {
            "configured": not missing,
            "missing": missing,
            "capabilities": {
                key: cap.as_dict() for key, cap in self.capabilities_map().items()
            },
            # §8.13：把"合同上要采哪几类 / 能不能真的采"分开报。
            # 只有一类都没验收时才为 False —— 前端因此不会把"配置齐了"读成"采得到数据"。
            "collection": {
                "object_types": list(self.collection_object_types()),
                "verified": self.supports_collection(),
            },
        }

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

    def missing_config(self) -> list[str]:
        missing = []
        if not settings.erp_base_url:
            missing.append("ERP_BASE_URL")
        if not settings.erp_app_key:
            missing.append("ERP_APP_KEY")
        if not settings.erp_app_secret:
            missing.append("ERP_APP_SECRET")
        return missing or ["ERP_PROVIDER"]

    async def push_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise ErpNotConfigured("、".join(self.missing_config()))

    async def fetch_order_status(self, external_id: str) -> dict[str, Any]:
        raise ErpNotConfigured("、".join(self.missing_config()))


class JushuitanAdapter(ErpAdapter):
    """聚水潭开放平台。

    公司服务器上已有 `erp-bridge`（聚水潭 + ERP321）对接服务，
    拿到它的调用方式后优先复用；这里按键值直连的形态实现，
    字段名按聚水潭开放平台的命名（wdt 前缀）。

    注意：聚水潭是否开放**建单**权限还没确认（08-待领导确认清单 §2-3），
    没开权限时 `push_order` 会返回业务错误，接口层会原样透出，不会静默忽略。

    ⚠️ 真实对接列（交接说明 §0.3 第 5 条）：企业 erp-bridge 的接口合同、
    聚水潭的签名算法与店铺授权都还没拿到。所以下面两项能力**如实**标成未验收：
    没有验收过的调用会在发请求之前被拒（`ErpNotVerified`），
    readiness 也只会显示"已配置未验证"。真实环境验收通过后，
    在这里把对应能力改成 `verified=True` 并在 `evidence` 里写清验收依据。
    """

    system_type = "ERP"
    label = "聚水潭"

    READ_CAPABILITY = ErpCapability(
        key="read",
        label="只读查询（订单状态）",
        verified=False,
        detail="签名算法尚未按官方资料实现，只读未验收（_call 目前只带 app_key）",
    )
    WRITE_CAPABILITY = ErpCapability(
        key="write",
        label="写入（建单/推单）",
        verified=False,
        detail="签名与建单权限均未验收，禁止发起真实建单",
    )
    #: §8.13 只读采集。**刻意不实现** `fetch_records`：聚水潭的订单/发货/售后
    #: 查询端点、分页参数（page_no/page_size 还是游标）、增量字段、店铺授权范围
    #: 都还没拿到官方资料或企业 erp-bridge 的接口合同。凭记忆写出来的字段名
    #: 一旦错了，会把"没采到"解释成"对方没有数据"，那比直接报未接通危险得多。
    #: 拿到资料后：实现 `fetch_records`，把这里改成 verified=True 并写 evidence。
    COLLECT_CAPABILITY = ErpCapability(
        key="collect",
        label="只读采集（订单/发货/售后分页增量）",
        verified=False,
        detail="端点、分页与增量参数、店铺授权均未按官方资料核实，未实现真实拉取",
    )

    def capabilities_map(self) -> dict[str, ErpCapability]:
        # 每次现读类属性（而不是在类体里拼一个固定 dict）：
        # 验收之后改的就是这两个类属性，readiness 必须立刻跟着变。
        return {
            "read": self.READ_CAPABILITY,
            "write": self.WRITE_CAPABILITY,
            "collect": self.COLLECT_CAPABILITY,
        }

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

    def missing_config(self) -> list[str]:
        missing = []
        if not settings.erp_base_url:
            missing.append("ERP_BASE_URL")
        if not settings.erp_app_key:
            missing.append("ERP_APP_KEY")
        if not settings.erp_app_secret:
            missing.append("ERP_APP_SECRET")
        return missing

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=settings.erp_base_url, timeout=30.0)
        return self._http

    async def _call(
        self, path: str, body: dict[str, Any], *, capability: str
    ) -> dict[str, Any]:
        """调一次聚水潭接口。

        `capability` 决定用哪项验收标记把关：没有验收过的能力**在发请求之前**就拒绝。
        这不是保守，是如实 —— 签名算法还没按官方资料实现，发出去的请求注定失败，
        而失败前它已经在对方系统里留下一次调用记录，反而更难解释。
        """
        missing = self.missing_config()
        if missing:
            raise ErpNotConfigured("、".join(missing))
        cap = self.capability(capability)
        if not cap.verified:
            # §8.12：配置齐全 ≠ 接通。真实写入更要卡死（"无法验证就禁用真实写入，
            # 绝不返回假成功"），只读同样如此：未验收的只读调用的返回值不能当证据。
            raise ErpNotVerified(
                f"{self.label}的「{cap.label}」尚未通过真实环境验收：{cap.detail}",
                api=path,
            )

        client = await self._client()
        # 聚水潭用 app_key + timestamp + sign 鉴权；sign 的算法要按对方文档实现。
        # 目前没有真实密钥、也没有验收，所以这里只带 app_key —— 而上面的能力校验
        # 已经保证了"没验收就不会走到这里"，不会把这种半成品请求发出去。
        payload = {**body, "app_key": settings.erp_app_key}
        try:
            response = await client.post(path, json=payload)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as error:
            # 4xx：请求到了对方且被拒 → 明确失败，可以重试/改报文。
            # 5xx：对方内部出错，可能已经处理了一半 → 结果未知。
            status = error.response.status_code if error.response is not None else None
            detail = _peer_message(error.response)
            if status is not None and 400 <= status < 500:
                raise ErpError(
                    f"HTTP {status}：{detail}",
                    api=path,
                    code=str(status),
                    error_class=classify_erp_error(status, detail),
                ) from error
            raise ErpResultUnknown(
                f"HTTP {status}：{detail}（对方内部错误，无法确认是否已建单）",
                api=path,
                code=str(status),
            ) from error
        except (httpx.HTTPError, httpx.TimeoutException) as error:
            # 传输层异常 = 结果未知：请求可能已经送到对方那边了。
            raise ErpResultUnknown(
                f"调用失败（{type(error).__name__}）：{error}；无法确认对方是否已受理",
                api=path,
            ) from error
        except ValueError as error:
            raise ErpResultUnknown(
                f"响应不是合法 JSON：{error}；无法确认对方是否已受理", api=path
            ) from error

        if not isinstance(data, dict):
            raise ErpResultUnknown(f"响应不是对象结构：{type(data).__name__}", api=path)
        # code 必须**显式给出**且为 0。此前写的是 data.get("code", 0)：
        # 没有 code 字段时默认成 0 = 成功，等于"对方只要返回 200 就算受理"，
        # 这是"没有可靠凭据也标已同步"的源头之一（§8.11）。
        raw_code = data.get("code")
        if raw_code is None:
            raise ErpError(
                "响应缺少 code 字段，无法确认对方是否受理",
                api=path,
                error_class=ERR_CLASS_UNCLASSIFIED,
            )
        try:
            code = int(raw_code)
        except (TypeError, ValueError) as error:
            raise ErpError(
                f"响应 code 不是整数：{raw_code!r}", api=path, error_class=ERR_CLASS_UNCLASSIFIED
            ) from error
        if code != 0:
            message = str(data.get("msg") or data.get("message") or data)
            raise ErpError(
                message,
                api=path,
                code=str(code),
                error_class=classify_erp_error(code, message),
            )
        return data

    async def push_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = await self._call("/open/orders/upload", payload, capability="write")
        # 逐项校验成功结构：结构不对一律当"结果未知"，不猜、不默认值。
        result = data.get("data")
        if result is None:
            result = {}
        if not isinstance(result, dict):
            raise ErpResultUnknown(
                f"响应 data 不是对象结构（{type(result).__name__}），无法取出外部订单号",
                api="/open/orders/upload",
            )
        items = result.get("items")
        if items is not None and not isinstance(items, list):
            raise ErpResultUnknown(
                f"响应 data.items 不是数组（{type(items).__name__}），无法取出外部订单号",
                api="/open/orders/upload",
            )
        first = (items or [{}])[0]
        if not isinstance(first, dict):
            first = {}
        return {
            "external_id": str(first.get("o_id") or result.get("o_id") or "").strip(),
            "external_code": str(first.get("so_id") or payload.get("so_id") or "").strip(),
            "raw": data,
        }

    async def fetch_order_status(self, external_id: str) -> dict[str, Any]:
        data = await self._call("/open/orders/query", {"o_id": external_id}, capability="read")
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


def _peer_message(response: httpx.Response | None) -> str:
    """尽力从错误响应里取对方的 code/msg，取不到就退回 HTTP 文本。"""
    if response is None:
        return "无响应内容"
    try:
        data = response.json()
    except Exception:
        return f"{(response.text or '')[:200]}"
    if isinstance(data, dict):
        return f"code={data.get('code')} msg={data.get('msg') or data.get('message') or data}"
    return str(data)[:200]


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


__all__ = [
    "CRM_STATUS",
    "ERROR_CODE_CLASS",
    "ERR_CLASS_CREDENTIAL",
    "ERR_CLASS_SHOP_PERMISSION",
    "ERR_CLASS_SIGNATURE",
    "ERR_CLASS_TRANSPORT",
    "ERR_CLASS_UNCLASSIFIED",
    "ErpAdapter",
    "ErpCapability",
    "ErpError",
    "ErpMappingMismatch",
    "ErpNotConfigured",
    "ErpNotVerified",
    "ErpResultUnknown",
    "ExternalPage",
    "JushuitanAdapter",
    "PROVIDERS",
    "STATE_CONFIGURED_UNVERIFIED",
    "STATE_FAULT",
    "STATE_LABELS",
    "STATE_NOT_CONFIGURED",
    "STATE_READONLY_VERIFIED",
    "STATE_WRITE_VERIFIED",
    "SYSTEMIC_ERROR_CLASSES",
    "StubAdapter",
    "classify_erp_error",
    "get_adapter",
    "reset_adapters",
]
