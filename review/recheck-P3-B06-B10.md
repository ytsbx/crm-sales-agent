# B06–B10 P3 逐条复核（证伪）

- 范围：B06-11..14、17..18；B07-11..14；B08-04；B09-04；B10-08..10，共 15 条。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b06_b10_p3.py -q`
- 结果：`15 failed, 0 passed`。全部成立，没有误报；其中 B08-04 是可直接用 asyncio 复现的机制错误。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B06-11 | **确认** | `serialize_record` 的 `quantity/amount` 仍 `float(row.x)`，无 `_exact`；外部事实在库里精确，出参丢末位 |
| B06-12 | **确认** | `EXCEPTION_QUEUE_STATUS` 只有 conflict/unmatched/unknown/skipped/not_sent，无 `FAILED`；匹配到订单但被状态守卫拦下的回调只进 sync_logs |
| B06-13 | **确认** | `_build_push_payload` 的 `"shop_id": None` 是硬编码；同一 ERP 账号多店铺无法区分 |
| B06-14 | **确认** | `list_mappings` 对有限范围只保留 `internal_id in scoped_orders`；待匹配行 `internal_id=NULL` 被整段过滤，销售看不到自己范围内的待匹配 |
| B06-17 | **确认** | `_fail_quietly` 的 `except Exception: pass` 没有 logger；失败落盘再失败时游标可能停在 running、lease 未清，服务端无告警 |
| B06-18 | **确认** | `push_order` 服务内先 `commit`，路由 `write_audit` 后第二次 `commit`；审计失败留下“已推单、无审计”，人工核对重试还会多一条 success IntegrationLog |
| B07-11 | **确认** | `run_reconciliation` service 内 `commit`，路由再 `write_audit`；审计失败留下无审计批次，重试命中 `run_key` 返回 reused 也补不上首次审计 |
| B07-12 | **确认** | `ContractDocument.request_key` 的 ORM 无 `UniqueConstraint`，只在迁移 `c7e3...` 建；`Base.metadata.create_all` 的测试库不拦重复 |
| B07-13 | **确认** | ERP 回调只校验 `X-ERP-Secret` 共享密钥，无 HMAC/时间戳/nonce；密钥泄露即可构造新事件改状态，事件键只能防同事件重放 |
| B07-14 | **确认** | `sync_approval` 两次 `visible_doc` 都不带 `for_update`，`store_snapshot` 前不复判 `status=="draft"`；并发签署/作废时会把审批快照写进已结束合同 |
| B08-04 | **确认（机制必现）** | `_cancel_safe` 在 `for attempt in range(...)` 内反复 `await asyncio.shield(coro)`；同一个 coroutine 第二次 shield 必 `RuntimeError: cannot reuse already awaited coroutine`。`except Exception` 记“清理失败”后返回，登记已被 `take_pending_objects` 取走，PDF 成孤儿且不再重试 |
| B09-04 | **确认** | `ApprovalRuleVersion.__table_args__` 只有普通 `Index("ix_approval_rule_versions_rule")`，无 `(rule_id, version_no)` 唯一；发布只靠 PG 咨询锁，非 PG/锁失效时同版两行 |
| B10-08 | **确认** | `send_prepared_approval` 先 `uploads.fill(payload)` 产生 OA 文件，随后 `lock_receipt` 发现业务状态变化走 `not_sent`；没有 `delete_approval`/cleanup，重试再上传一份 |
| B10-09 | **确认** | `start_sample_approval` 对 `sample.status in {"pending","rejected"}` 无条件置 `"pending"`；OA `create` 返回 approved/rejected/withdrawn 时本地仍显示审批中 |
| B10-10 | **确认** | `send_prepared_approval` 只有 `except client.OaError`；`lock_receipt` 的 `AppError`、第三方 `KeyError/TypeError` 直接抛，`row.status` 停在 `submitting`，路由审计也不执行 |

## 三条要单独说明的

### B08-04：不是“可能重试失败”，是同一 coroutine 第二次 shield 必炸

`_cancel_safe` 的实现：

```python
for attempt in range(_retries + 1):
    try:
        return await asyncio.shield(coro)
    except asyncio.CancelledError:
        ...
        logger.info("...重试第 %d 次", attempt + 1)
```

`asyncio.shield(coro)` 第一次会把同一个 coroutine 包成内部 Task；外层被取消后，内部 Task 仍持有这个 coroutine。第二次再 `shield(coro)` 时，Python 直接报：

```text
RuntimeError: cannot reuse already awaited coroutine
```

这不是概率问题，是只要发生“清理期间再被取消一次”就必现。更麻烦的是 `cleanup_pending_objects` 入口已经 `take_pending_objects` 把登记取走并清空，所以失败后没有第二次清理机会，PDF 成为永久孤儿。

修法：每次重试新建 coroutine（把清理逻辑写成 `lambda: cleanup(...)` 工厂），或第一次就 `task = asyncio.ensure_future(coro)`，后续 `await asyncio.shield(task)`；不要把同一个 coroutine 反复 shield。

### B07-13：共享密钥只能证明“知道密钥”，不能证明“这条事件未被篡改”

`X-ERP-Secret` 是静态共享密钥。它能挡住没有密钥的调用，但挡不住：

- 密钥从日志/配置/离职人员泄露后，攻击者构造一条全新的 `event_key` 和 `remark`；
- 旧请求被重放（没有时间戳窗口）；
- 中间人改 body 后原样带密钥转发（没有 body 签名）。

服务层的事件键幂等只能防“同一事件重复”，防不了“同一密钥下的新事件”。建议至少升级为 `HMAC-SHA256(timestamp + nonce + raw_body)`，服务端校验时间窗（如 ±5 分钟）和 nonce 去重；共享密钥只作为过渡，并在网关加来源 IP/双向 TLS。

### B06-18 / B07-11 / B10-10：同一个“外部副作用先发生、本地审计后写”的模式

三条都是同一类事务边界问题：

- B06-18：ERP 推单/人工核对 `service.commit()` 成功，路由再 `write_audit`；
- B07-11：对账 `run_reconciliation` 内 `commit`，路由再写审计；
- B10-10：OA `create_approval` 已建单，路由才写审计；非 `OaError` 还会留下 `submitting`。

共同后果：**外部动作已经生效，本地审计/失败状态没落**。接口返回 500，用户重试可能命中幂等回放，但首次审计永远补不上；如果外部系统没有删除/回滚接口，就只剩“到对方后台核对”。

正确做法不是简单“把审计挪到 service 里”，而是统一“外部副作用登记”模型：发起前写 `attempt`，外部返回后写 `receipt`，审计与 receipt 同事务；失败/未知结果写 `needs_review` 而不是只抛异常。至少要保证“外部已发生”这件事在本地一定有一条可查记录，哪怕审计表写失败也要落到独立 outbox。

## 本批最大漏项：失败落盘/审计写入本身失败，没有 dead-letter 兜底

审查员分别列了 B06-17（失败落盘再失败被 `pass`）、B06-18/B07-11（审计后写失败）、B10-10（非 OaError 留 submitting），但没有把它们归到同一个“观测/审计链路也会坏”的问题：

- 采集失败时，`_fail_quietly` 自己再失败就 `pass`，游标停在 running，没人知道；
- 推单/对账成功后，审计写失败就只剩外部系统有记录，CRM 查不到；
- OA 发起遇到非 `OaError`，`submitting` 留库，审计不写。

这三类都不是金额错，但会让运维在故障时**失去唯一的本地证据**。尤其 ERP 对账/推单是跨系统事实，一旦本地审计缺失，只能去对方后台翻，排障成本远高于一条 outbox 记录。

建议统一加一个最小 outbox：外部副作用/审计事件先写本地 `outbox` 行（同事务），再由后台投递；投递失败进 dead-letter 并告警。至少 `_fail_quietly` 的二次失败要写本地文件/内存告警，不能 `pass`。

## 残余

- B08-04 的 asyncio 复用机制已用源码确认，未在合同清理真实取消场景注入；`RuntimeError` 是 Python 语义必现。
- B07-13 的 HMAC 缺失是路由源码事实，未做密钥泄露攻击演练。
- B06-17/B06-18/B07-11/B10-10 的“审计写失败”未做故障注入；当前是事务顺序与异常分支源码证据。
- B10-08 的 OA 删除接口是否存在未核实；即使不支持删除，也应在错误文案/待清理表登记。
