"""外部集成的**共用词汇表**：状态、对象类型、差异类型、稳定键规则。

为什么单独一个文件：§8.13（外部只读采集与对账）与 §8.14（SKU 主数据权威）共用
同一批承载表（`external_records` / `external_object_mappings` / `integration_diffs`
/ `external_source_registry`），两边都要按同一套字符串判断状态。散在各 service 里
写字面量，第一次改名就会一边改一边漏，队列的筛选条件从此对不上。

**这里只放"我们自己的口径"**，不放任何外部系统的字段名/状态词/错误码：
聚水潭与简道云的字段名、签名、店铺授权都还没拿到（交接说明 §0.3 第 5 条），
凭记忆在这里写一个字段名，等于把猜的接口当成真的接通。
"""

# ---------------------------------------------------------------- 对象类型

#: 外部事实的对象类型。前三个是需求点名的采集对象（订单 / 发货 / 售后）；
#: 带 `_item` 的是明细行，用 `parent_key` 挂到单据上，**拆单/合单/部分退换
#: 都靠它们在行级说清楚"这一行属于哪张单"**，从而不会重复计。
OBJECT_ORDER = "order"
OBJECT_SHIPMENT = "shipment"
OBJECT_AFTERSALE = "aftersale"
OBJECT_ORDER_ITEM = "order_item"
OBJECT_SHIPMENT_ITEM = "shipment_item"
OBJECT_AFTERSALE_ITEM = "aftersale_item"

#: 需要按"外部编号 → 本地对象"建映射的对象类型。
#: 明细行也在里面：一行拆多行 / 多单合一行时，只有行级映射才能说明白这行属于谁。
MAPPED_OBJECT_TYPES = (
    OBJECT_ORDER,
    OBJECT_ORDER_ITEM,
    OBJECT_SHIPMENT,
    OBJECT_SHIPMENT_ITEM,
    OBJECT_AFTERSALE,
    OBJECT_AFTERSALE_ITEM,
    "customer",
    "sku",
)

#: 主对象（单据/客户/SKU）：稳定键就用外部编号本身；明细行的稳定键是 `父键#行键`。
PRIMARY_OBJECT_TYPES = (OBJECT_ORDER, OBJECT_SHIPMENT, OBJECT_AFTERSALE, "customer", "sku")

#: 采集适配层的三类对象（§8.13 先做只读采集的正是这三类）。
COLLECT_OBJECT_TYPES = (OBJECT_ORDER, OBJECT_SHIPMENT, OBJECT_AFTERSALE)

#: 不分店铺时用的店铺标识。刻意用 `*` 而不是 NULL：唯一约束里 NULL 不参与比较，
#: 用 NULL 会让同一个 (系统, 对象类型) 插出任意多行水位，断点续拉就失去唯一性。
ALL_SHOPS = "*"

# ---------------------------------------------------------------- 采集水位状态

CURSOR_IDLE = "idle"                       # 空闲：上一次跑完了，可以接着采
CURSOR_RUNNING = "running"                 # 正在采（带租约，过期可被接管）
CURSOR_FAILED = "failed"                   # 跑过但失败，断点留着下次续
#: 下面两个是"如实显示未接通"的两种状态，与 `failed` 刻意分开：
#: 未配置/未验收**一次请求都没发出去**，把它标成"失败"会让人去查对方的日志。
CURSOR_NOT_CONFIGURED = "not_configured"
CURSOR_NOT_VERIFIED = "not_verified"

# ---------------------------------------------------------------- 映射与差异

MATCH_PENDING = "pending"      # 待匹配：外部对象还没对上本地对象
MATCH_MATCHED = "matched"      # 已匹配
MATCH_CONFLICT = "conflict"    # 有歧义（同名多家 / 一个外部号对上多个本地对象）
MATCH_IGNORED = "ignored"      # 人工判定"不需要匹配"（例如对方的历史脏数据）

DIFF_DOMAIN_ORDER = "order_reconcile"  # §8.13 对账差异
DIFF_DOMAIN_SKU = "sku_master"         # §8.14 主数据差异

DIFF_OPEN = "open"
DIFF_RESOLVED = "resolved"     # 已处理（采纳某一方 / 人工指定）
DIFF_IGNORED = "ignored"       # 人工判定不是差异

# --- §8.13 对账差异类型 -----------------------------------------------------
#: 外部有、本地没有（未知客户/SKU/订单）：进待匹配，补齐后可重放。
DIFF_MISSING_LOCAL = "missing_local"
#: 本地推过、但这一期在对方系统里没看到：可能漏采，也可能对方没建单。
DIFF_MISSING_EXTERNAL = "missing_external"
#: 金额不一致（本地订单金额 vs 外部事实金额）。
DIFF_AMOUNT_MISMATCH = "amount_mismatch"
#: 发货数量大于订单数量：**双算的自动探针**（拆单/合单被重复计一次就会命中）。
DIFF_OVER_SHIPPED = "over_shipped"
#: 退货数量大于发货数量：同样是不双算的自检。
DIFF_OVER_RETURNED = "over_returned"
#: 同一个外部编号出现在两个店铺：本地按店铺分行、不串，但显式提示有人可能合并了。
DIFF_CROSS_SHOP_NUMBER = "cross_shop_same_number"
#: 订单行引用的外部 SKU 还没匹配到本地 SKU。
DIFF_UNMATCHED_SKU = "unmatched_sku"
#: 订单上的客户还没匹配到本地客户。
DIFF_UNMATCHED_CUSTOMER = "unmatched_customer"
#: 出现了外部售后/退款事实：**必须人工核定来源与时点后才能影响本地**，
#: 在此之前一律不动回款/应收（用户尚未拍板退货退款核定口径）。
DIFF_AFTERSALE_REVIEW = "aftersale_needs_review"

ORDER_DIFF_TYPES = (
    DIFF_MISSING_LOCAL,
    DIFF_MISSING_EXTERNAL,
    DIFF_AMOUNT_MISMATCH,
    DIFF_OVER_SHIPPED,
    DIFF_OVER_RETURNED,
    DIFF_CROSS_SHOP_NUMBER,
    DIFF_UNMATCHED_SKU,
    DIFF_UNMATCHED_CUSTOMER,
    DIFF_AFTERSALE_REVIEW,
)

#: 这些差异在核定前**不可能**自动作用到本地财务数据上：核定动作只记录结论，
#: 由人再去走已确认口径的流程（外部售后未经核定不得覆盖本地回款）。
DIFF_NEVER_AUTO_APPLIES = (DIFF_AFTERSALE_REVIEW, DIFF_AMOUNT_MISMATCH, DIFF_CROSS_SHOP_NUMBER)

# --- §8.14 SKU 主数据差异类型 ----------------------------------------------
#: 外部值与本地当前值不一致（名称/规格等一般字段）。
DIFF_FIELD_CONFLICT = "field_conflict"
#: 单位冲突：件/套/箱这类口径不一致，直接影响报价数量含义。
DIFF_UNIT_CONFLICT = "unit_conflict"
#: 包装冲突：包装方式或装箱数与本地不一致。
DIFF_PACKAGE_CONFLICT = "package_conflict"
#: 外部值与**已人工确认的版本**不一致：改它等于改一个已经对客用过的口径。
DIFF_CONFIRMED_DIFFERS = "confirmed_value_differs"
#: 增量上报了空值，而本地/已确认版本有值。**默认不覆盖**（不抹人工销售资料）。
DIFF_NULL_OVERWRITE = "null_overwrite"
#: 来源里的编码改名了（改码）。老编码保留成历史，本地编码要改需人工确认。
DIFF_CODE_RENAME = "code_rename"
#: 来源标记停用。本地停用要人工确认，且**不能破坏历史报价**。
DIFF_STOPPED_SOURCE = "stopped_source"
#: 同名不同码：三系统里名字一样、编码不同 —— **绝不自动合并**。
DIFF_SAME_NAME_DIFF_CODE = "same_name_diff_code"
#: 外部身份还没匹配到本地 SKU（本地还没有这条 SKU）。
DIFF_UNMATCHED_SKU_SOURCE = "unmatched_sku_source"

SKU_DIFF_TYPES = (
    DIFF_FIELD_CONFLICT,
    DIFF_UNIT_CONFLICT,
    DIFF_PACKAGE_CONFLICT,
    DIFF_CONFIRMED_DIFFERS,
    DIFF_NULL_OVERWRITE,
    DIFF_CODE_RENAME,
    DIFF_STOPPED_SOURCE,
    DIFF_SAME_NAME_DIFF_CODE,
    DIFF_UNMATCHED_SKU_SOURCE,
)

# --- §8.14 SKU 外部身份的匹配状态 -------------------------------------------
IDENTITY_PENDING = "pending"     # 待匹配（本地还没有这条 SKU）
IDENTITY_MATCHED = "matched"     # 已匹配
IDENTITY_RENAMED = "renamed"     # 已改码：老行保留，指向新编码
IDENTITY_STOPPED = "stopped"     # 来源已停用
IDENTITY_CONFLICT = "conflict"   # 同名不同码 / 命中多条，需人工裁定

# --- §8.14 字段权威状态 ------------------------------------------------------
AUTH_UNVERIFIED = "unverified"              # 来源未核实 → 前端显示"待核实"
AUTH_PENDING = "pending_confirmation"       # 有待确认差异
AUTH_CONFIRMED = "confirmed"                # 已人工确认（正式报价可用）
AUTH_CONFLICT = "conflict"                  # 来源之间冲突，待裁定

# --- 差异核定结论（两个域共用同一套词） --------------------------------------
RESOLUTION_KEEP_LOCAL = "keep_local"
RESOLUTION_TAKE_EXTERNAL = "take_external"
RESOLUTION_MANUAL = "manual"
RESOLUTION_IGNORE = "ignore"
#: §8.14 专用：按来源的新编码改本地 SKU 编码（老编码留成可反查的历史）。
RESOLUTION_RENAME_LOCAL = "rename_local"
#: §8.14 专用：按来源的停用结论停用本地 SKU（不删、不动历史快照）。
RESOLUTION_DISABLE_LOCAL = "disable_local"


# ---------------------------------------------------------------- 来源台账（§8.14 + §8.12）

#: 来源核实状态。默认未核实：这一轮拿不到任何真实来源资料，
#: 所以字段来源一律显示"待核实"，**不默认任一系统为主**。
SOURCE_UNVERIFIED = "unverified"
SOURCE_VERIFIED = "verified"

#: 外部系统标识（本轮的取值；真实接入后按实际系统填）。
#: 只放"我们这边怎么称呼它"，不代表已经连上。
SYSTEM_JUSHUITAN = "JUSHUITAN"
SYSTEM_JIANDAOYUN = "JIANDAOYUN"
SYSTEM_CRM = "CRM"
SYSTEM_MANUAL = "MANUAL"

# ---------------------------------------------------------------- 稳定键

#: 明细行稳定键的分隔符。用 `#` 是因为外部单号里几乎不会出现它，
#: 而且拼出来的键肉眼可读（`SO-1#2` 一眼看出是 SO-1 的第 2 行）。
LINE_KEY_SEP = "#"


def line_dedupe_key(parent_key: str, line_key: str) -> str:
    """明细行的稳定去重键：父单键 + 行键。

    为什么明细也要稳定键：拆单（一单拆成多次发货）与合单（多单并一次发货）
    在"单号"层面无法表达，只有在行级记住"这一行属于哪张单的第几行"，
    汇总时才不会把同一批货算两遍。
    """
    return f"{parent_key}{LINE_KEY_SEP}{line_key}"
