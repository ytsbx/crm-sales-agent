# B11–B15 P3 逐条复核（证伪）

- 范围：B11-12、B12-06、B13-10/11、B14-06/07/08、B15-07/08，共 9 条。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b11_b15_p3.py -q`
- 结果：`9 failed, 0 passed`。全部成立，无误报；其中 B15-08 建议升 P2。

## 结论表

| 编号 | 结论 | 关键点 |
|---|---|---|
| B11-12 | **确认** | `RoleCreate.data_scope: str = "self"`、`RoleUpdate.data_scope/status: str | None`，无 Literal/枚举；`resolve_data_scope` 对不认识的 scope 走 `return "self"`，非法值静默降为只看自己 |
| B12-06 | **确认** | `upload_file` 先 `storage.save_upload`，再构造 `FileRecord(file_name=file.filename, mime_type=file.content_type)`；`file_name String(255)`、`mime_type String(128)`、`BusinessFile.category String(32)`，超长先写盘再 500，靠 `discard_unregistered_upload` 清理 |
| B13-10 | **确认** | `create_customer` 的 `request_key` 可选；无键时只返回“本次未带请求键，弱网重试可能产生重复客户”，仍创建第二条；单建入口没有强制幂等 |
| B13-11 | **确认** | `create_tag/update_tag` 的 `except IntegrityError` 直接 `await session.rollback()`，回滚整个 session，不只标签 INSERT/UPDATE；当前路由只多写一条 audit，影响有限，复用到更大事务会丢别的写入 |
| B14-06 | **确认** | `CreateCustomerRequest` 的 `name/short_name/region/address/level/source/remark` 全是裸 `str`；`Customer` 列宽 200/100/64/255/8/32，超长到 DB 才 500；`level` 也无字典校验 |
| B14-07 | **确认** | `_token_cache` 是普通 dict，`access_token` 无 per-key `asyncio.Lock`；缓存键只有 `corp_id:ext/contact`，不含 secret 版本/指纹；并发首次取 token 会重复请求，轮换 secret 后旧 token 最长再活 2 小时 |
| B14-08 | **确认** | `batch_tag` 的 `write_audit` 只有 `customer_ids/tag_ids`，无 before；`mode=replace` 删掉的旧标签无法从审计复算 |
| B15-07 | **确认** | 失败一次后重试成功：`finish_job(job, success=1, fail=job.fail_count)` 先算 partial，下一行又 `job.status = "success"`；`fail_count` 仍为 1，状态与计数矛盾 |
| B15-08 | **确认（建议升 P2）** | `materialize_business_notifications` 只对 `source_type == 'quote'` 判 `deleted_at`；customer/lead/opportunity/order/sample 已删来源仍生成主管通知；`visibility` 也只对 quote 的权限 SQL 加 `deleted_at.is_(None)` |

## 两条要单独说明的

### B14-07：token 缓存的问题不是“多打一次接口”，是密钥轮换后仍用旧 token

`access_token`：

```python
cache_key = f"{settings.wecom_corp_id}:{'ext' if external else 'contact'}"
cached = _token_cache.get(cache_key)
if cached and cached[1] > time.time():
    return cached[0]
```

两个缺口：

1. 没有 per-key 单飞锁，多个协程同时首次取 token 会各自 `gettoken`；
2. 缓存键不含 secret 的指纹/版本，运维轮换 `WECOM_*_SECRET` 后，进程内旧 token 仍会被返回，直到 TTL 到期（约 2 小时）。如果企微侧已禁用旧 secret，这段时间所有企微调用都用旧 token 失败，排查时很容易误判成“企微接口挂了”。

修复：`asyncio.Lock` per cache key 做单飞；缓存键加 `hashlib.sha256(secret.encode()).hexdigest()[:8]`（不要存 secret 本身）或配置版本号，密钥变更立即从空缓存重取。

### B15-08：软删过滤只做了 quote，其他单据的通知仍会生成/可见

`materialize_business_notifications`：

```python
if source_type == 'quote' and source.deleted_at is not None:
    continue
```

`visibility` 的权限 SQL：

```python
if kind == 'quote':
    stmt = stmt.where(model.deleted_at.is_(None))
```

customer/lead/opportunity/order/sample 都有 `deleted_at`，但这两处都没判。后果不是“界面多一条”，而是：

- 事件积压期间来源被软删，materialize 仍生成主管通知；
- 通知中心/时间线按原单权限仍能看到已删客户/线索/订单的标题与内容；
- 对已删客户发“请跟进”会误导业务，也让已删数据继续在通知里留存。

建议把 `source_type -> model` 的软删判断统一成一张映射，materialize 和 visibility 共用；不要继续写 `if source_type == 'quote'`。这条建议从 P3 升 P2：它涉及已删数据的可见性和误导性通知，不只是体验。

## 本批最大漏项：B15-08 的软删可见性应升 P2，且要覆盖“通知 payload 已生成”之后才软删的窗口

审查员把 B15-08 写成“已删非 quote 单据仍生成/可见主管通知”，但没有强调触发窗口：事件已经落库、`notification_processed_at` 为空，来源在 materialize 之前被软删。这个窗口在积压/调度延迟时很常见。

更关键的是，它不是单点：`materialize` 生成通知、`visibility` 判可见，两处各漏一次；即使 materialize 修了，历史已生成的通知在 visibility 里仍能按原单权限看到。修复必须同时做“生成时过滤 + 读取时过滤”，并覆盖所有有 `deleted_at` 的业务类型；只改 materialize 会留下已生成通知的可见性漏洞。

## 残余

- B11-12 的非法 scope 落库后实际路由行为未造数据；`resolve_data_scope` 的 fallback 是源码事实。
- B12-06/B14-06 的超长字段未实际打 PostgreSQL；列宽与 Pydantic 元数据是确定事实。
- B14-07 的并发/密钥轮换未起多协程实测；缓存键与无锁是源码事实。
- B15-08 的“事件积压后软删”未造数据；两处过滤只对 quote 是源码事实。
