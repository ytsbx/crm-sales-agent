# 报价驱动型销售 CRM + Sales Agent V1.1
## 开发总设计文档（最终冻结基线）

> 版本：V1.1  
> 状态：开发基线  
> 目标：统一客户、商机、报价、订单、回款与销售任务，并通过企业微信和 Sales Agent 形成销售闭环。
>
> **这是冻结的设计基线，不是实现现状。** 实现与基线的差异（哪些没做、哪些口径改了、
> 哪些是刻意取舍）统一记在 `10-项目现状与交接说明`；当前进度与待办见 `21-交接说明-2026-10-04.md`。
> 例：RAG/pgvector 知识库、Outbox 事件表、Redis 缓存、微服务拆分——基线里写过，实现中**未采用**。

---

# 1. 项目定位

本系统定位为：

> **以客户和商机为核心、以报价为关键业务节点、以企业微信为客户入口、以 Sales Agent 为智能执行层的销售 CRM。**

系统不做成普通“客户登记 + 表格管理”的 CRM，也不做企业微信智能表格的替代品。

核心目标：

1. 统一客户、联系人、商机、报价、订单、回款等销售数据；
2. 建立统一核价和报价机制，解决业务员报价不一致、价格权限不清的问题；
3. 建立从线索到成交、回款、复购的完整闭环；
4. 接入企业微信，承接内部成员、外部联系人、客户转接与离职继承；
5. 通过 Sales Agent 实现查询、分析、智能核价、报价草稿、任务生成与风险预警；
6. 与 ERP/MES 对接，不重复建设生产、采购、仓储等履约能力。

---

# 2. 系统边界

## 2.1 CRM 负责

- 线索
- 客户
- 联系人
- 客户归属
- 公海池
- 商机
- 商机需求
- 产品与 SKU 销售资料
- 成本与价格体系
- 物流试算
- 核价
- 报价
- 报价版本
- 报价审批
- 跟进
- 任务
- 样品（可选启用）
- 成交 / 失单
- 销售订单
- 应收计划
- 实际回款
- 客户复购
- 数据分析
- Sales Agent
- 企业微信集成
- ERP/MES 集成
- 权限、审批、文件、通知、审计

## 2.2 ERP / MES 负责

- 采购
- 库存
- 生产
- BOM 主数据
- 质检
- 入库
- 出库
- 发货
- 生产履约

CRM 仅同步和展示订单履约关键状态，不复制整套 ERP/MES。

---

# 3. 最终业务主流程

```text
客户来源
│
├─ 企业微信
├─ 展会
├─ 官网
├─ Excel
├─ 老客户介绍
└─ 手工录入
        ↓
      Lead
        ↓
识别 / 去重 / 归一
        ↓
┌───────────────┬───────────────┐
↓                               ↓
Customer                      Contact
客户主体                        联系人
└───────────────┬───────────────┘
                ↓
        分配业务负责人
                ↓
          Opportunity
              商机
                ↓
       OpportunityItem
          商机需求明细
                ↓
               SKU
                ↓
     ┌──────────┴──────────┐
     ↓                     ↓
  Pricing              Logistics
  核价引擎               物流试算
     └──────────┬──────────┘
                ↓
              Quote
                ↓
          QuoteVersion
         V1 / V2 / V3...
                ↓
            QuoteItem
                ↓
          持续 FollowUp
                ↓
          商机最终结果
         ↙            ↘
      成交              失单
       ↓                 ↓
 SalesOrder          LossRecord
       ↓                 ↓
   ERP / MES            客户培育
       ↓                 │
    履约状态              │
       ↓                 │
ReceivablePlan          │
       ↓                 │
 PaymentRecord          │
       ↓                 │
      复购 ──────────────┘
       ↓
New Opportunity
```

横向能力贯穿所有主流程：

```text
Task / Approval / Permission / Audit / Notification / File
Knowledge / Sales Agent / Analytics / Integration
```

---

# 4. 核心领域关系

```text
Department 1:N User

Lead → Customer / Contact / Opportunity

Customer 1:N Contact
Customer 1:N Opportunity
Customer 1:N FollowUp
Customer 1:N Task
Customer 1:N SalesOrder

Opportunity 1:N OpportunityItem
Opportunity 1:N Quote
Opportunity 1:N FollowUp
Opportunity 1:N Task

Product 1:N SKU

SKU 1:N ProductCost
SKU 1:N PriceRule
SKU N:N OpportunityItem
SKU N:N QuoteItem

Quote 1:N QuoteVersion
QuoteVersion 1:N QuoteItem
QuoteVersion 1:N QuoteCharge

SalesOrder 1:N SalesOrderItem
SalesOrder 1:N ReceivablePlan
ReceivablePlan 1:N PaymentRecord

ApprovalInstance 1:N ApprovalRecord

AgentSession 1:N AgentMessage
AgentSession 1:N AgentAction
AgentAction 1:N AgentExecution
```

---

# 5. 关键设计原则

## 5.1 Customer 与 Contact 分离

- Customer：公司、采购主体或个人客户主体；
- Contact：客户内部具体联系人。

一个客户可以有多个联系人。

---

## 5.2 企业微信外部联系人不等于 CRM 客户

```text
企业微信 ExternalContact
        ↓
CRM Contact
        ↓
CRM Customer
```

企业微信内部成员则映射到 CRM User。

---

## 5.3 企业微信跟进关系单独建模

一个外部联系人可能同时被多个内部员工跟进，因此不能只在 Contact 上保存一个 follow_userid。

```text
WeComExternalContact
        ↓ 1:N
WeComFollowRelationship
```

---

## 5.4 Lead 必须独立存在

Lead 是进入 CRM 的原始销售线索，不能直接跳到 Customer。

线索状态建议：

- 待分配
- 已分配
- 跟进中
- 已转客户
- 无效

线索转化时支持：

- 关联已有客户
- 创建新客户
- 创建联系人
- 可选创建商机

---

## 5.5 客户必须支持去重与归一

匹配依据：

- 企业名称
- 手机
- 邮箱
- 域名
- 统一社会信用代码
- 企业微信信息
- 联系人信息

禁止系统静默自动合并。

---

## 5.6 商机是销售主业务对象

```text
Customer 1:N Opportunity
```

一个客户可以同时存在多个采购项目。

---

## 5.7 商机需求必须使用 OpportunityItem

```text
Opportunity
↓
OpportunityItem
↓
SKU
```

禁止把多个 SKU 用逗号字符串或 JSON 粗放塞进 Opportunity。

---

## 5.8 报价必须版本化

```text
Opportunity
↓
Quote
↓
QuoteVersion
↓
QuoteItem
```

已发送或已审批通过的报价版本不可原地覆盖。

---

## 5.9 报价必须保存历史快照

价格中心保存“当前价格”，报价保存“当时价格”。

QuoteItem 至少保存：

- SKU 名称快照
- 规格快照
- 成本快照
- 包装成本快照
- 物流成本快照
- 标准价快照
- 建议价快照
- 最低价快照
- 实际报价
- 利润快照
- 利润率快照

QuoteVersion 还应保存：

- 币种
- 汇率快照
- 汇率来源
- 汇率时间

---

## 5.10 附加费用独立于报价商品

物流、税费、包装、折扣等不应全部硬塞入单个 QuoteItem。

```text
QuoteVersion
├─ QuoteItem
└─ QuoteCharge
```

QuoteCharge 类型：

- logistics
- packaging
- tax
- discount
- service
- other

---

## 5.11 FollowUp 与 Task 明确分工

- FollowUp：已经发生的销售行为；
- Task：未来需要执行的动作。

例如：

```text
今天联系客户
↓
FollowUp

客户要求周五再联系
↓
Task(due_at=周五)
```

---

## 5.12 创建人与负责人分离

离职继承：

- created_by：保留历史创建人；
- owner_id：允许转移当前负责人。

---

## 5.13 应收计划与实际回款分离

```text
SalesOrder
↓
ReceivablePlan
↓
PaymentRecord
```

一个应收节点可以对应多次实际回款。

---

# 6. 一级模块

1. 工作台
2. 线索中心
3. 客户中心
4. 商机中心
5. 报价中心
6. 订单中心
7. 产品中心
8. 价格中心
9. 销售任务
10. 数据分析
11. Sales Agent
12. 系统设置

---

# 7. 线索中心

功能：

- 线索池
- 分配
- 领取
- 回收
- 废弃
- 跟进
- 查重
- 转客户
- 转联系人
- 转商机

线索转化流程：

```text
Lead
↓
查重
↓
已有 Customer?
├─ 是 → 关联
└─ 否 → 创建
↓
创建 / 关联 Contact
↓
可选创建 Opportunity
```

---

# 8. 客户中心

包含：

- 客户列表
- 联系人
- 公海池
- 客户等级
- 客户标签
- 客户归属
- 企业微信同步
- 离职继承
- 客户合并
- 客户时间线

客户详情 Tab：

- 概览
- 联系人
- 商机
- 跟进记录
- 报价
- 订单
- 文件
- 操作日志

---

# 9. 企业微信集成

## 9.1 内部成员

```text
企业微信成员
↓
CRM User
```

## 9.2 外部联系人

```text
企业微信 ExternalContact
↓
CRM Contact
↓
Customer
```

## 9.3 待归一工作台

用于处理：

- 未绑定客户的外部联系人
- 疑似重复客户
- 同一联系人多员工跟进
- 企业微信客户转接
- 离职继承

---

# 10. 商机中心

商机阶段建议：

```text
新询盘
→ 需求确认
→ 产品推荐
→ 核价
→ 已报价
→ 样品
→ 商务谈判
→ 待下单
→ 成交
```

任意合适阶段可进入失单。

商机核心数据：

- 客户
- 联系人
- 负责人
- 预计金额
- 预计成交时间
- 当前阶段
- 竞争对手
- 客户需求
- 风险状态
- 下一步动作

---

# 11. 产品中心

CRM 维护销售侧产品资料：

- Product
- SKU
- 分类
- 图片
- 规格
- 材质
- 颜色
- 包装方式
- 箱规
- 装箱数
- 重量
- 体积
- MOQ
- 销售说明
- 产品知识

完整 BOM 由 ERP/MES 维护，CRM 不重复建设。

---

# 12. 价格中心

支持：

- 采购成本
- 生产成本
- 包装成本
- 加工成本
- 标准指导价
- 客户等级价
- 阶梯价
- 客户特殊价
- 最低保护价
- 价格权限
- 利润规则
- 价格历史

---

# 13. 核价引擎

输入：

- Customer
- Customer Level
- SKU
- Quantity
- Country
- Packaging
- Shipping Method
- Payment Terms
- Currency
- Exchange Rate
- Sales Channel
- Target Margin

输出：

- Cost
- Recommended Price
- Recommended Range
- Minimum Price
- Profit
- Profit Rate
- Approval Required

---

# 14. 物流能力

用于报价试算，不做完整物流 TMS。

支持：

- 运输方式
- 目的地
- 计费重
- 体积
- 运输时效
- 运费试算
- 多方案比较
- 物流费用快照

---

# 15. 报价中心

支持：

- 从商机生成报价
- 多版本
- 报价复制
- 重新核价
- 版本对比
- 报价审批
- 报价有效期
- PDF
- 邮件发送
- 发送记录
- 客户接受 / 拒绝
- 报价失效
- 转销售订单

状态建议：

```text
草稿
→ 待审批
→ 已通过
→ 已发送
→ 已接受 / 已拒绝 / 已失效
```

---

# 16. 报价审批

审批触发场景：

- 低于业务员权限价
- 低于最低保护价
- 特殊折扣
- 特殊付款条件
- 特殊账期

原则：

> 超出权限的价格不能由业务员直接对外发送。

---

# 17. 跟进与任务

FollowUp 支持关联：

- Customer
- Contact
- Opportunity
- Quote
- Order

Task 来源：

- 人工
- 系统规则
- AI
- 商机
- 报价
- 订单
- 回款

自动任务示例：

- 报价后 3 天未跟进；
- A 类客户 14 天未联系；
- 样品签收 2 天未回访；
- 商机预计成交日期临近；
- 应收即将到期。

---

# 18. 样品模块

若业务存在频繁寄样，启用：

```text
SampleRequest
↓
SampleItem
↓
SampleShipment
↓
客户签收
↓
样品反馈
```

如业务量较少，可暂不独立成一级模块，仅作为商机内子功能。

---

# 19. 成交与失单

```text
                  ┌─ 成交 ─→ SalesOrder
Opportunity Result
                  └─ 失单 ─→ LossRecord
```

失单原因：

- 价格
- MOQ
- 交期
- 产品
- 竞争对手
- 付款条件
- 项目取消
- 无真实需求
- 其他

失单后允许：

- 进入培育
- 设置重新联系日期
- 后续重新创建商机

---

# 20. 订单与回款

成交后：

```text
QuoteVersion
↓
SalesOrder
↓
ERP / MES
↓
履约状态
↓
ReceivablePlan
↓
PaymentRecord
↓
回款完成
↓
复购
↓
New Opportunity
```

复购不重新创建 Lead，也不重新创建 Customer。

---

# 21. Sales Agent

Agent 不是普通聊天框，而是销售执行层。

建议 Tool：

- LeadTool
- CustomerTool
- ContactTool
- OpportunityTool
- ProductTool
- PricingTool
- LogisticsTool
- QuoteTool
- FollowUpTool
- TaskTool
- OrderTool
- PaymentTool
- AnalyticsTool

风险等级：

### L1：自动执行

- 查询
- 总结
- 分析
- 生成任务
- 生成草稿

### L2：用户确认后执行

- 修改客户
- 修改商机
- 新建报价版本
- 新建订单

### L3：审批后执行

- 低价报价
- 超权限折扣
- 特殊付款条件
- 重要客户转移
- 删除重要数据

Agent 所有动作必须留痕。

---

# 22. Agent 数据模型

```text
AgentSession
↓
AgentMessage

AgentAction
├─ proposed
├─ awaiting_confirmation
├─ approved
├─ executing
├─ success
├─ failed
└─ cancelled

AgentExecution
ToolExecution
```

必须可审计：

- AI 建议了什么
- 谁确认
- 调用了什么 Tool
- 修改了什么数据
- 执行是否成功
- 是否重试

---

# 23. 权限设计

角色：

- 业务员
- 销售主管
- 销售经理
- 管理层
- 客服 / 售后
- 财务
- 管理员

数据范围：

- self
- department
- department_and_sub
- all

权限维度：

- 菜单权限
- 操作权限
- 数据权限
- 价格权限
- 审批权限

---

# 24. 审计

必须记录：

- 操作人
- 时间
- 业务对象
- 动作
- 修改前
- 修改后
- IP
- 来源（Web/API/Agent/Integration）

重点审计：

- 价格修改
- 报价修改
- 客户负责人转移
- 商机阶段变化
- 审批
- 订单修改
- 回款确认
- 删除
- Agent 执行

---

# 25. 工作台

工作台不独立保存业务数据，只做聚合。

展示：

- 今日待办
- 待跟进客户
- 进行中商机
- 待审批报价
- 风险商机
- 回款提醒
- 最近动态
- AI 建议

---

# 26. 数据分析

客户：

- 新增客户
- 活跃客户
- 沉睡客户
- 客户来源
- 客户等级
- 复购客户

商机：

- 新增商机
- 阶段分布
- 成交率
- 失单率
- 平均成交周期

报价：

- 报价次数
- 平均版本数
- 平均让价
- 报价转成交率
- 低价审批率

销售人员：

- 客户数
- 跟进数
- 商机数
- 报价数
- 订单额
- 回款额

产品：

- 询盘最多 SKU
- 报价最多 SKU
- 成交最多 SKU
- 失单最多 SKU
- 利润表现

---

# 27. 前端菜单

```text
工作台

线索中心
├─ 线索池
├─ 我的线索
└─ 待转化

客户中心
├─ 客户列表
├─ 联系人
├─ 公海池
├─ 企业微信待归一
└─ 客户详情

商机中心
├─ 商机列表
├─ 销售漏斗
└─ 商机详情

报价中心
├─ 报价列表
├─ 待审批
├─ 审批记录
└─ 报价详情

订单中心
├─ 销售订单
├─ 应收计划
└─ 回款记录

产品中心
├─ 产品
└─ SKU

价格中心
├─ 成本
├─ 价格策略
├─ 阶梯价格
├─ 客户价
├─ 价格权限
└─ 价格历史

销售任务

数据分析

Sales Agent

系统设置
├─ 用户
├─ 部门
├─ 角色权限
├─ 企业微信
├─ ERP/MES
├─ 物流
├─ 商机阶段
├─ 客户等级
├─ 编号规则
├─ 字典
└─ 系统配置
```

---

# 28. 技术建议

- Frontend：React + TypeScript
- Backend：Python FastAPI
- Database：PostgreSQL
- Cache / Queue：Redis
- File：MinIO / OSS
- Search：PostgreSQL FTS / Elasticsearch（后续）
- AI：LLM + Tool Calling
- Knowledge：pgvector 或独立 RAG 服务
- Integration：企业微信 API、ERP/MES API、物流 API

V1 Agent：

> 单 Agent + Tool Calling + 权限校验 + 审批机制

---

# 29. 开发阶段

## Phase 1：主数据与入口

- Auth
- User / Department / Role
- 企业微信成员同步
- 外部联系人同步
- Lead
- Customer
- Contact
- 产品
- SKU

## Phase 2：核心销售链

- Opportunity
- OpportunityItem
- FollowUp
- Task
- Timeline

## Phase 3：报价闭环

- Cost
- PriceRule
- Pricing
- Logistics
- Quote
- QuoteVersion
- QuoteItem
- QuoteCharge
- Approval

## Phase 4：成交与履约

- Win / Loss
- SalesOrder
- ERP/MES
- ReceivablePlan
- PaymentRecord

## Phase 5：运营能力

- Dashboard
- Analytics
- Notification
- File
- Audit

## Phase 6：智能化

- AgentSession
- AgentAction
- AgentExecution
- 智能核价
- 报价草稿
- 风险分析
- 自动任务

---

# 30. V1 最终验收链路

```text
企业微信出现李经理
↓
同步为 ExternalContact
↓
进入待归一工作台
↓
识别属于 ABC GmbH
↓
绑定 Contact + Customer
↓
分配给张三
↓
创建德国浴桶商机
↓
添加 2 个 OpportunityItem
↓
匹配 SKU
↓
读取成本
↓
物流试算
↓
Pricing Engine 核价
↓
生成 Quote V1
↓
客户反馈价格偏高
↓
记录 FollowUp
↓
自动创建下一 Task
↓
生成 Quote V2
↓
V2 低于业务员权限
↓
发起 Approval
↓
主管审批通过
↓
发送报价
↓
客户接受
↓
商机成交
↓
生成 SalesOrder
↓
同步 ERP/MES
↓
ERP/MES 返回履约状态
↓
建立 ReceivablePlan
↓
记录多次 PaymentRecord
↓
回款完成
↓
进入复购维护
↓
创建 New Opportunity
```

这条链完整跑通，才算 V1 主业务闭环完成。
