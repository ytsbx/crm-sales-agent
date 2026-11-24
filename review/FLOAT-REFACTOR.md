# float→Decimal 专项重构 · 盘点与分批计划

日期：2026-11-24。分支 `acceptance-wrapup`（基线 25fcfc92，全量 2200 项通过）。
范围（用户指令）：数据库金额列已是 Numeric/Decimal（87 列、0 Float），**不动库**；只改 Python 中间层
（service 计算、接口序列化、schema 注解），让钱从库到接口全程不碰 float。
每批跑隔离全量回归 `backend/.venv/bin/python -m pytest backend/tests -o addopts=''`；
无业务取舍直接修，口径/精度/序列化格式取舍先问用户；每批回写 QUALITY_PROGRESS.md，提交只推 GitHub；服务器只读、不部署。

对应台账挂起项：B17-03 / B18-02 / B19-03 / B20-01 / B20-07 / B21-01 / B21-04，
以及已确认未闭合的 B01-02/06/11、B03-05、B06-11、B09-02/05、B12-01、B13-07、B16-02（残余 float 出口）、B17-02。

## 0. 关键发现：三个"不写字面 float 也会变 float"的隐式出口

1. **FastAPI JSON 出口（全局）**：所有路由返回 dict → 默认 `jsonable_encoder` 把 Decimal 转 float。
   本机实测（fastapi 0.141.1 / pydantic 2.13.5）：`Decimal('99999999999999.99')` → `99999999999999.98`。
   **只改 service 层不改出口，精度照样在最后一跳丢**。需要自定义 JSONResponse 按 Decimal 原值渲染
   JSON 数字字面量（前端契约不变），或改字符串（契约变化）。→ 待用户决策 Q1。
2. **Agent 工具结果**：`agent/runtime.py:476/579` `json.dumps(result)` 无 default，Decimal 会直接
   TypeError；`commentary.py:132` 已有 `default=str`。工具出口改 Decimal 时必须同步处理该边界。
3. **JSONB 快照**：`core/audit.py:51` `json_safe` 与 `product/master.py:195` `_json_value` 把 Decimal
   转 float 存 JSONB（审计留痕/主数据版本快照不可精确复算，B12-01/B20-07）。
   `bizdoc/service.py:156` 已有"统一转字符串、不转 float"的先例。→ 待用户决策 Q2（历史记录不重写）。

另：pydantic v2 `model_dump_json` 对 Decimal 默认输出**字符串**；本项目路由基本返回 dict，不走该路径，
但新增 response_model 时要注意。

## 1. 盘点清单（grep "float" 全量 198 处 / 52 文件，按处置分类）

### A. 金额/数量序列化出口与中间计算 —— 本专项改造对象（按批次分组）

**批2 报价链（quote + approval）**
| 位置 | 内容 | 台账 |
|---|---|---|
| quote/service.py:248-249 | `_f` Decimal→float 序列化 helper（版本/明细出参总闸） | B01-02 |
| quote/service.py:988 | 版本 total_amount float 出参 | B01-02 |
| quote/service.py:2414 | `total_amount_cny=float(tier_amount)` **只进 summary/审计留痕**；分档比较本已 Decimal（2778 行 `tier_amount <= Decimal(cap)`，2026-11-24 核实，并发会话复核结论一致）。批2 修留痕精度，不重复修比较 | B01-01（比较部分已排除） |
| quote/service.py:2503-2549 | 审批信息 quoted_price/quoted_price_cny/minimum_price/profit_rate float | B01-02 |
| quote/service.py:2667-2668 | 整单 quoted_price_cny/profit_rate float | B01-02 |
| quote/service.py:2737/2797 | 审批摘要 original_amount float | B01-02 |
| quote/service.py:2792 | authorized_min_margin float（比率，见 Q3） | — |
| quote/router.py:1308/1338/1401 | 调价 old_price/审计 quoted_price float | B01-11 |
| quote/router.py:1444/1504/1540 | 审计/通知 total_amount float | B01-11 |
| quote/router.py:2054-2065 | 手动重算接口 before/after 四金额 float（响应+审计） | B01-11 |
| approval/router.py:96 | 审批列表/详情 total_amount float | B09-05 |
| approval/rules_engine.py:439 | 规则上下文 total_cny float（阈值边界误命中） | B09-02 |
| approval/rules_engine.py:495/503 | 毛利率/逐行毛利率 float 后参与阈值比较 | B09-02（比较必须 Decimal；展示见 Q3） |
| approval/rules_engine.py:539 | 版本让价差额 float | B09-02 |

**批3 订单/回款（order + payment + drafts）**
| 位置 | 内容 | 台账 |
|---|---|---|
| order/service.py:36-37 | `_f` 序列化 helper | B01-02 同标准 |
| order/router.py:214 | 手工建单响应 total_amount float | — |
| order/drafts.py:59/61 | 草稿明细 quantity/unit_price float 出参（前端整份拷贝回提交，持久化精度链） | 进度 1347 行挂起线索 |
| payment/service.py:52-53 | `_f` 序列化 helper | B01-02 同标准 |
| payment/router.py:1267 | plan_allocated_amount float | — |
| payment/schema.py:75-94 | 收款比例 `list[float]` 注解（比例本身见 Q3；HALF_EVEN 拆分口径**挂起**） | B03-15 |

**批4 定价/物流/产品（pricing + product + master）**
| 位置 | 内容 | 台账 |
|---|---|---|
| pricing/service.py:42-43 | `_f` 序列化 helper（特殊价/费率，前端回填再保存会改价） | B03-05 / F03-02 |
| pricing/logistics.py:31-32 | `_f` round(float,4)（费率序列化） | B03-05 |
| pricing/logistics.py:399/402/403 | 试算 amount/by_weight_amount/by_volume_amount float | B03-05 |
| pricing/router.py:1291/1310/1613 | quantity/cost/amount float 出参 | B03-05 |
| pricing/router.py:1992 | SKU weight float | B20-07 |
| pricing/service.py:771/1309 | 角色折扣上限 resolve_discount_limit float（比率，见 Q3） | — |
| product/service.py:14-15 | `_number` 物理量序列化（3-4 位小数丢尾） | B20-07 |
| product/master.py:195 | `_json_value` 主数据版本快照 Decimal→float 存 JSONB。**注意（并发会话复核指出，批4 核实）：该序列化也用于本地值比较**，改字符串前须核对 normalize_field_value 比较链，不能顺手换 | B20-07（Q2） |

**批5 客户/商机/询价（customer + opportunity + inquiry）**
| 位置 | 内容 | 台账 |
|---|---|---|
| customer/service.py:24-26 | `_f` 序列化 helper | B13-07 |
| customer/service.py:305 | 客户报价聚合 amount float | B13-07 |
| customer/router.py:762 | 客户报价当前版本金额 float | B13-07 |
| opportunity/service.py:25-26 | `_number`（预计金额/明细数量/目标价） | B19-03 |
| opportunity/router.py:365/423 | 漏斗/看板按阶段 SUM float | B20-01 |
| opportunity/router.py:688/700/715 | 360 概览报价版本金额/需求数量 float | B20-01 |
| inquiry/service.py:127-128 | 询价 quantity/target_price float | B19-03 |
| inquiry/router.py:206/208 | 询价转报价 quoted_price/minimum_price_snapshot float | B19-03 |

**批6 分析/目标（analytics，最大块）**
| 位置 | 内容 | 台账 |
|---|---|---|
| analytics/service.py:84-85 | `_f` float(value or 0) 无舍入总闸 | B17-03 |
| analytics/service.py:605/610 | 趋势 orders/payments `dict[str,float]` 累加 | B17-03 |
| analytics/service.py:1296/1299 | 产品分析 profit/quantity float 累加 | B17-03 |
| analytics/service.py:1636-1647 | 定价分析 discounted/by_level float 列表、quoted_price float（折扣率计算基数） | B17-03 |
| analytics/targets.py:110-111/577/579 | 目标值 sales_target/repeat_customer_target float | B18-02 |
| analytics/targets.py:516/534/981 | `Decimal | float` 混合类型注解 | B18-02 |
| analytics/targets.py:761/778 | 销售/复购差额 variance float（金额！） | B18-02 |
| analytics/targets.py:762/780 | 达成率 round(float,4)（比率，见 Q3） | B18-02 |
| analytics/target_bases.py:523-529 | 年度 signed/shipped/received/repeat_net 桶 float(round()) | B17-03/B18-02 |
| analytics/target_actuals.py:136 | 存档明细 amount round(float,2) | B18-02 |
| analytics/router.py:495-496 | 目标保存响应 sales_target/repeat_customer_target float | B18-02 |

**批7 打样/洞察（sample + product_insight）**
| 位置 | 内容 | 台账 |
|---|---|---|
| sample/service.py:34-35 | `_f` round(float,4)：费用/运费/数量/工时/积分 | B21-01 |
| product_insight/router.py:105 | price_assumption 序列化 float | B21-04 |
| product_insight/router.py:142 | 评审快照 Decimal→float 存 JSONB | B21-04（Q2） |
| product_insight/router.py:761 | 转需求描述 float 格式化 | B21-04 |

**批8 Agent + ERP 入站序列化（tools + insights + commentary + collection）**
| 位置 | 内容 | 台账 |
|---|---|---|
| agent/tools.py:182-185 | `_money` quantize 后转 float | B17-02 |
| agent/tools.py:188-192 | `_measure` quantize(0.0001) 后转 float（数量/重量/计费重） | B17-02 |
| agent/tools.py:508-518 | 待回款/订单额/已收 float(round()) | B17-02 |
| agent/tools.py:669/1650 | 明细 quantity float | B17-02 |
| agent/tools.py:776/778/1560 | 工具入参 quantity/quoted_price `float` 注解 | B17-02 |
| agent/tools.py:980-984/1070 | 应收计划/逾期 remaining float(round()) | B17-02 |
| agent/tools.py:1482 | SKU weight float | B17-02 |
| agent/insights.py:40-43 | `_f` quantize HALF_UP 后转 float（B16-02 已修舍入、出口仍 float） | B16-02 残余 |
| agent/commentary.py:108 | `_compact` quantize 后转 float | B16-02 残余 |
| erp/collection.py:1304-1305 | serialize_record quantity/amount float 出参 | B06-11 |
| （边界）agent/runtime.py:476/579 | json.dumps 需支持 Decimal（见隐式出口 2） | — |

**批1 基础设施（先行，依赖 Q1/Q2 决策）**
- 自定义 JSON 出口（Decimal 精确渲染）+ `core/audit.py:51` json_safe + 回归基线。

### B. 非金额 float —— 保留不动（有明确理由）

| 位置 | 内容 |
|---|---|
| core/rate_limit.py:18/21 | 限流时间戳 |
| core/similarity.py:72 | 字符串相似度比率 |
| analytics/usage.py:102 | 百分位 ratio 参数 |
| analytics/service.py:970 | 商机停留天数 durations |
| analytics/service.py:1963 | 准时率 _rate 百分比 |
| notification/service.py:76/86 | 重试次数/退避分钟配置解析 |
| settings/service.py:627-633 | get_number 通用数值配置读取（重试/退避等非金额参数；批内核实调用方无金额用途） |
| wecom/client.py:85、dingtalk/client.py:135、dingtalk/service.py:1763-1773 | token 缓存/登录态时间戳 |
| bizdoc/pdf.py:123 | PDF 列宽 |
| customer/duplicates.py:84 | 查重相似度 score |
| core/errors.py:81 | pydantic 错误码键名 "float_parsing" |

### C. 注释/docstring/类型守卫 —— 保留（守卫按批适配 Decimal）

- 纯注释：csvio.py:36、importing.py:186、bizdoc/service.py:156、lead/schema.py:81、opportunity/schema.py:92、
  pricing/schema.py:328、order/service.py:764、quote/router.py:1812、targets.py:392/743、payment/service.py:1039、
  agent/tools.py:329/478/786/929、insights.py:785、product_insight/router.py:138、settings/service.py:414。
- isinstance 类型守卫（保留，必要时补 Decimal 分支）：rules_engine.py:98、rules_router.py:67-68（有限性校验）、
  settings/service.py:421（_strict_amount 入口闸）、product_insight/router.py:72/144、commentary.py:105、
  erp/collection.py:149、agent/tools.py:174。
- settings/service.py:347-354 `_strict_ratio`：折扣比例上限校验（比率，见 Q3）。

### D. 挂起 —— 本轮不动（既有待决策/外部契约，需单独批准）

| 位置 | 内容 | 原因 |
|---|---|---|
| erp/service.py:172-186 | 推单报文 pay_amount/qty/price/amount float | B06-04：聚水潭实际接口数字类型/精度未核实，外部契约取舍 |
| quote/service.py:985/2739/2799、pricing/router.py:2013、rules_engine.py:439(to_cny 外币分支) | 汇率快照/汇率表序列化 float | 外币遗留分支；CNY-only 规则下是否保留/删除待用户单独决定（进度 1310 行） |
| payment 收款比例拆分 HALF_EVEN（B03-15） | split_amounts 默认银行家舍入 | 与 core/money HALF_UP 口径取舍，待决策；本轮只动 ratios 注解不动舍入 |

## 2. 分批计划（每批：改造→定向精度回归→全量回归→回写进度→提交只推 GitHub）

1. **批1 基础设施**：JSON 出口 Decimal 精确渲染（按 Q1 决策）+ audit json_safe（按 Q2）+ 大额边界回归用例。
2. **批2 报价链**：quote/service+router、approval/router+rules_engine（含分档/阈值 Decimal 比较）。
3. **批3 订单/回款**：order/service+router+drafts、payment/service+router（ratios 拆分口径不动）。
4. **批4 定价/物流/产品**：pricing/service+logistics+router、product/service+master。
5. **批5 客户/商机/询价**：customer、opportunity、inquiry。
6. **批6 分析/目标**：analytics/service+targets+target_bases+target_actuals+router（最大，必要时拆 6a/6b）。
7. **批7 打样/洞察**：sample/service、product_insight/router。
8. **批8 Agent/ERP 入站**：agent/tools+insights+commentary（含 runtime json.dumps 边界）、erp/collection。

测试契约原则：既有把 float 固定为期望值的用例（如 195.0）改为 Decimal/精确字符串比较，**不删断言、不放容差**；
每批新增 99999999999999.99 / 四位单价 / 三位数量边界用例。前端不在本轮范围（服务器只读、不部署）；
JS Number 解析 15-16 位上限是前端既有问题，本轮后端改造不使其变差。

## 3. 待用户决策

- **Q1 JSON 出口格式**：✅ 已定（2026-11-24，用户验收标准"钱不能算错"）：精确数字字面量
  （自定义渲染器，契约不变、前端零改动）+ **编辑/保存往返接口补 `*_exact` 字符串字段**
  （吸收 C 方案收益：编辑页读 exact 原样传回，堵死"打开再保存少一分"；前端会话跟进消费）。
- **Q2 审计/JSONB 快照**：✅ 已定：改存字符串（bizdoc 先例，历史不重写）。批1 已落地 audit；
  product/master、洞察评审快照随批4/批7。
- **Q3 比率/百分比字段**：✅ 已定：计算/阈值比较用 Decimal，纯比率输出保留 float。
- **Q4（批1 新发现）幂等回放精度**：✅ 已定（主人拍板走方案2，2026-11-24 落地）。
  complete 快照改存**精确 JSON 原文**（json_safe decimal_as_str=None 保留 Decimal +
  exact_json_dumps 整段文本，JSON 列顶层字符串）；_judge 回放按顶层类型解包：
  str→json.loads(parse_float=Decimal) 逐字读回，历史 dict→原样 float 形状（不重写历史）。
  业务响应恒为 dict，顶层 str 不可能是老快照，无需标记键、无撞车风险。
  payment/router 两处改传原始 body（不再预先 json_safe(False)）。对外数字契约不变。
  新增 test_decimal_q4_replay 2 项（16 位大金额回放逐位精确、老 dict 原样回放）；
  旧存储断言更新为原文+解包；全量 2238 通过。
- **Q5（批2 新发现）规则阈值配置侧精度**：✅ 已定（主人拍板走方案3 阈值改字符串配置，
  2026-11-24 落地）。RuleCondition 增 model_validator：数值字段阈值统一归一
  `exact_threshold_str`（精确十进制字符串，'1e5' 展开；接受 int/float/数字字符串，
  非法/非有限抛 ValueError→422）；枚举/布尔字段原样。_validate_payload 契约更新为
  "数字或数字字符串"。evaluate_conditions 认字符串阈值（Decimal 逐位解析，
  InvalidOperation 静默未命中守卫扩围）；conditions_overlap 上下界归一 Decimal
  （str/float 混比不炸）；_fmt 数字字符串与数字同展示口径。历史 float 行求值走
  Decimal(str()) 老路、发布重建 RuleCondition 时自愈成字符串。**前端配置页跟进**：
  GET 返回阈值为字符串，编辑页按字符串读写（数字入参仍兼容）。
  新增 test_decimal_q5_threshold_config 8 项；conflicts 存储断言收紧字符串；
  全量 2246 通过。settings get_number 比率配置（default_min_margin 等）≤15 位
  往返安全，16 位边界同类残留记台账不扩范围。
- **Q6（批4 新发现）master._json_value 三链耦合**：待决策，批4 未碰。该 helper 同时服务
  ① JSONB 版本快照/authority.source_value 落库、② 本地值比较（local_value vs incoming、
  confirmed vs incoming）、③ build_sku_diff_key 差异指纹。改 Decimal→字符串后：
  历史 confirmed 行仍是 float，str≠float 会把"没变"判成"变了"→ 伪差异/伪冲突；
  指纹 payload 形状变化 → 存量 pending 差异键一次性失配。候选：比较侧统一
  Decimal(str(x)) 归一 + 接受指纹一次性 churn（需评估存量差异行数），或维持 float
  仅快照侧字符串化（比较链继续 float，精度边界依旧）。涉及业务口径，先问主人。

## 4. 执行状态

- [x] 盘点（本文件，2026-11-24）
- [x] Q1/Q2/Q3 用户决策（2026-11-24，按"钱不能算错"定合并方案）
- [x] 批1 基础设施（jsonio 渲染器 + main 出口 + audit 字符串化 + 回放契约隔离；全量 2212 通过）
- [x] 批2 报价链（quote/service _f 透传 Decimal、offending/summary/审计/重算对比去 float、
      approval/router 出参、rules_engine 金额上下文 Decimal + 阈值归一 + trace 字符串化；
      比率保留 float；全量 2216 通过）
- [x] 批3 订单/回款（order/service _f 透传 + serialize_item 补 quantity_exact、order/router 建单
      total_amount、drafts 数量/单价、payment/service _f 透传 + unallocated 零值 Decimal、
      payment/router plan_allocated_amount；ratios 注解与 HALF_EVEN 拆分口径挂起不动；
      连带：11 个测试文件本地 FastAPI() 统一接 DecimalJSONResponse（与主应用同款出口，
      修跨测试编码器注册污染）、batch12 审计断言收紧为 exact 字符串逐字相等、
      finance precision 断言 float→Decimal；新增批3 边界 4 项；全量 2220 通过）
- [x] 批4 定价/物流/产品（pricing/service _f 透传 + 比率出参显式 float + resolve_discount_limit
      注解纠正 Decimal + rule_payload 补三 exact；logistics _f 透传（库刻度无损）+ 试算三金额
      Decimal + by_*_exact；pricing/router 四出口去 float；product/service _number 透传；
      master._json_value 挂起 Q6 未碰；旧 float 断言收紧、物流漂移防线改用丢尾数回传验证；
      新增批4 边界 3 项；全量 2223 通过）
- [x] 批5 客户/商机/询价（customer/service _f 透传 + 订单币种聚合 Decimal、customer/router
      当前版本金额、opportunity/service _number 透传、opportunity/router 漏斗/看板 SUM +
      360 概览版本金额/数量 Decimal、inquiry/service 数量/目标价 + exact、inquiry/router
      转报价 quoted/minimum + exact；零旧断言破坏；新增批5 边界 3 项；全量 2226 通过）
- [x] 批6 分析/目标（analytics/service _f 透传 + 趋势/产品累加 Decimal + 定价分析 by_level
      Decimal、折扣率 Decimal 计算 float 输出、average_discount 显式 float（Q3）；
      targets 目标值/差额 Decimal + target_snapshot 补 exact、达成率保持 Decimal 算 float 出；
      target_bases 四桶去 float（round 本已 Decimal）；target_actuals 存档 round(float,2)→
      Decimal 精确半偶（修 double 伪影边界，2.675→2.68）；router 目标保存响应去 float；
      analytics/service.py 选择性暂存 8 hunk，另一会话 team_summary hunk 留工作区；
      新增批6 边界 3 项；全量 2229 通过）
- [x] 批7 打样/洞察（sample/service _f 透传（库刻度≤3 位，round4 无损）；insight/router
      price_assumption Decimal + exact、_content_snapshot 按 Q2 存原值字符串（只存只展示、
      无值比较链，已核实）、转需求描述 format 'f'；新增批7 边界 3 项；全量 2232 通过）
- [x] 批8 Agent/ERP 入站（tools _money/_measure quantize 口径不变出口 Decimal、待回款/币种分组/
      应收 remaining/明细 quantity/SKU weight 去 float；入参 float 注解保留并注释（模型 JSON
      number 入口契约，下游 pydantic 归一 Decimal，入站精度边界与 Q4 同类）；insights _f、
      commentary _compact Decimal + _facts_text 改 exact_json_dumps；runtime 两处模型上下文
      json.dumps→exact_json_dumps（否则 Decimal TypeError）；erp/collection serialize_record
      数量/金额去 float；ERP 推单报文挂起不碰；旧 float 断言收紧；新增批8 边界 4 项；
      全量 2236 通过）

## 5. 收尾残留核对（2026-11-24，八批完成后全量 grep 复核）

允许保留类：① 比率/百分比输出 float（Q3：quote profit_rate/authorized_min_margin、
targets 达成率、rules_engine 毛利率、analytics average_discount、pricing 权限比率）；
② 挂起三项（ERP 推单报文 erp/service:172-186 B06-04、外币 fx 遗留分支、收款比例
HALF_EVEN B03-15 含 payment/schema ratios 注解）；③ 待决策 Q4 回放分支 audit.py:56
（decimal_as_str=False）、Q5 settings get_number/_strict_ratio 配置侧、Q6 master:195；
④ 非金额 float（dingtalk 登录态/token 过期、notification 重试次数、bizdoc PDF 列宽、
相似度/百分位/停留天数等）；⑤ Agent 工具入参 float 注解（入口契约，已注释）；
⑥ 注释与 isinstance 守卫。除此之外 app/modules+app/core 无金额 float 强转残留。

备注：工作区另有离职处理会话的未提交改动（user/task/dingtalk/analytics 状态过滤、前端设置页、
test_user_offboarding.py 及进度日志一节），本专项提交只挑本专项文件，不携带其代码改动；
QUALITY_PROGRESS.md 为共享日志，提交时会包含其已写入的日志文本。