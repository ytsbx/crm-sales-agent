# P3 逐条复核：B21–B24（24 条）

- 范围：B21 9 条、B22 6 条、B23 6 条、B24 3 条。
- 方式：逐条打开实现与上下游，再用最小断言测试证伪；不复用审查员结论。
- 测试：`review/repro/test_b21_b24_p3.py`
- 命令：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b21_b24_p3.py -q`
- 结果：**24 failed, 0 passed**，失败项全部对应“缺陷成立”的断言。

## 一、逐条结论

| 编号 | 结论 | 代码证据与复核 |
|---|---|---|
| B21-05 | 确认 | `product/io_router.py:69-83`、`215-240`：两个导出 POST 都收裸 `payload: dict`，`keyword` 传数字/对象时 `keyword.strip()` 直接 500；`_export_products`/`_export_skus` 用 `.scalars().all()` 一次性拉全表，无分页或流式上限。 |
| B21-06 | 确认（降为幂等语义） | `product_insight/router.py:617` 的 `review_insight` 全文不读 `payload.request_key`；同 key 重试不会产生第二条评审，但会返回 422“已评审”，客户端无法判断“上次其实成功了”。不是数据损坏，是重试语义不完整。 |
| B21-07 | 确认 | `InsightCreate` 无 `request_key`；`create_insight` 直接 `session.add`。双击/弱网重试会生成两条内容相同的洞察记录。 |
| B21-08 | 确认 | `product/router.py:366-405`：`enable_sku`/`disable_sku` 审计只写 `action`，没有 `before=`/`after=`；`sample/service.py:563-577` 的 `record_opportunity_touch` 直接改商机 `next_action`，不写审计。SKU 状态和商机下一步都能被改而查不到前后值。 |
| B21-09 | 确认 | `sample/service.py:255-258`：`like = f"%{keyword.strip()}%"`，`%`/`_` 未转义。搜 `%` 会命中全部样品单；洞察列表同型（`product_insight/router.py:382-383`）。 |
| B21-10 | 确认 | `sample/sources.py:62-66`：来源负责人直接取 `opportunity.owner_id or customer.owner_id or user.id`，只校验客户/商机可见，不校验该负责人是否在职、是否在操作者数据范围内；与洞察 `_assert_assignee` 的三项判据不一致。离职账号仍可被写成新打样单负责人。 |
| B21-11 | 确认 | `product/router.py:587-600`：`category`/`remark` 是裸 `str | None`，无 `max_length`；`BusinessFile` 对应 `String(32)`/`String(255)`。超长时文件已上传成功，挂载 INSERT 才 500，用户重试会重复挂同一文件。 |
| B21-12 | 确认 | `product_insight/schema.py:87-92`：`images: list[str]` 只校验 URL 安全，不限条数、不限单条长度，原样进 JSON 列。可提交超长列表撑大行/响应。 |
| B21-13 | 确认 | `sample/schema.py:71-96`：`SampleCreate` 无 `request_key`；`create_from_source` 已强制 `request_key`，两个入口幂等口径不一致。手动打样双击会出两张申请单。 |
| B22-07 | 确认 | `sample/router.py:713` 的 `revise_sample` 不读 `request_key`；`SampleRequest` 模型无 `(parent_id, version)` 唯一约束，迁移也没有。双击会生成两份 `version=2`，且两套子单都能继续审批。 |
| B22-08 | 确认（4 个子项） | `task/schema.py:16-27`：`priority: str` 自由字符串；`title` 只有 `min_length=1`，纯空白能过；`TaskBatchComplete.task_ids: list[int]` 无上限；`TaskCreate` 无 `request_key`。批量完成逐条处理，列表可拉长请求。 |
| B22-09 | 确认 | `task/router.py` 的 `complete_task` 审计只有 `action`，无 `before` 状态；取消、改期、批量完成同型。改期不记原截止日，批量只记计数，事后无法还原单条前后。 |
| B22-10 | 确认 | `sample/router.py:599-645` 的 `approve_sample`：只写 `after=detail`，无 `before` 状态/金额/依据文件；寄样、签收、客户反馈、登记制作、客户确认同型。审批链只能看到结果，看不到从什么状态改来。 |
| B22-11 | 确认 | `settings/schema.py:73` 的 `prefix` 是无字符集约束的字符串；`numbering.period_max_number` 用 `LIKE f"{head}%"` 探测最大序号，`%`/`_` 未转义。前缀填 `%` 会把其他规则的编号一起纳入取最大值，生成跳号/撞号。不是 SQL 注入，是编号正确性问题。 |
| B22-12 | 确认 | `task/scanning.py:9-14` 用 `except Exception` 吞掉方言探测异常且不打日志。PostgreSQL 下探测失败会走 SQLite 分支，静默跳过 advisory lock；并发扫描可能重复建任务。 |
| B23-04 | 确认 | `settings/schema.py:6-9`、`95-110`、`124-135` 全部是裸 `BaseModel`；对应列是 `String(64/255/32/128)`。`SettingInput.key/description`、字典 `type/code/label/remark`、等级 `code/name/remark` 超长都到 INSERT 才 500，不是 422。 |
| B23-05 | 确认 | `settings/router.py:176-182`：只有 `merge_fields`（`hard_floor/export/dedup_scoring/notification_retry`）加 `with_for_update`；`approval_levels` 不在其中，仍是“读整块 JSON → 改 → 写回”。两个管理员并发保存会丢更新，后写覆盖先写。字典/等级/编号规则先查后插也无锁，唯一键冲突兜底成 500 而非 409。 |
| B23-06 | **确认，建议升 P2** | `settings/service.py:2013-2021`：`run_auto_tasks` 先把 `customer.next_followup_at` 覆盖为未完成跟进任务的最小 `due_at`，**之后**才读 `agreed = customer.next_followup_at` 判断“已约定下次跟进”。用户手工约定的未来日期会先被抹掉，豁免判断读到空值，于是仍然生成“客户被冷落”任务。这是行为错误，不是单纯并发丢更新。 |
| B23-07 | 确认 | `settings/service.py:1617`、`1810`：`decide_candidate`/`assert_no_protection` 调 `protection_detail(session)` 不传 `customer_id`，单客户操作扫全库保护规则；`service.py:802` 同型。数据量大时单条复核会退化成全表扫描。 |
| B23-08 | 确认 | `settings/router.py:385`：`candidate_ids: list[int]` 无 `max_length`；路由内逐条 `decide` 并 commit。一次请求可塞大量候选，拉长事务和请求时间。 |
| B23-09 | 确认 | `core/importing.py:466-476`：`zlib.decompress(compressed)` 无 `max_length`。`preview_token` 由客户端持有，可构造高压缩比数据造成内存/CPU 消耗。 |
| B24-01 | 确认（配置型） | `main.py:102-108`：`allow_origins=settings.cors_origin_list` 与 `allow_credentials=True` 同时存在，没有“`*` 与凭据互斥”的启动校验。默认配置是 localhost，不触发；运维把 `CORS_ORIGINS` 配成 `*` 就会进入浏览器规范禁止的组合，表现为跨域凭据随机失效。 |
| B24-02 | 确认 | `main.py:171-194`：健康检查 `except Exception: db_ok=False`，不打日志、不带异常；`SELECT 1` 没有超时，数据库连接池耗尽时探活会一直挂到连接超时，监控拿到的是“请求超时”而不是“DB 不可用”。 |
| B24-03 | 确认 | `core/similarity.py:58`：`normalize_name` 只做 `.strip()` + 去普通空格/全角空格，不处理名字中间的 `\t`、`\n`、`\r`。`"宁波\t宏远"` 与 `"宁波宏远"` 归一化后仍不同，导入查重/合并会漏判同一家公司。 |

## 二、需要单独说明

1. **B23-06 不只是 P3。** 现有实现先覆盖再判断，等于把“销售约了下个月联系”的承诺当成没有。建议按 P2 修：先读原值判断豁免，再用锁和 `refresh_next_followup_at` 回写；或者只更新派生缓存列，原约定单独存字段。
2. **B22-07 的破坏性高于普通 P3。** 不只是多一条记录：两份 V2 都能继续审批、制作、寄样，后续冻结关系按 `parent_id` 判断会同时冻结/串版。建议至少加 `(parent_id, version)` 唯一约束 + `request_key`。
3. **B24-01 只在运维改配置时触发。** 当前默认 `localhost:5173` 是安全的，不应按“线上已越权”处理；但应在启动时直接拒绝 `*` + credentials，避免环境变量换错。
4. **B21-06 与 B21-07 要分开看。** B21-06 是“重复评审返回 422”，不产生重复数据；B21-07 是“重复创建”，会真出两条。前者是体验/幂等语义，后者是数据完整性。
5. **B21-10 的“负责人”不是越权读，而是错误归属。** 客户/商机本身已过可见性校验；问题在把可能离职/范围外的 owner 直接写成新单负责人，后续待办、通知、绩效都会挂错人。

## 三、本批最大漏项

- **B23-06**：自动任务扫描覆盖用户手工约定，直接造成“已约定仍催办”。这是本批唯一建议升级的条目。
- **B22-07**：修订版没有唯一版本约束，双击即可制造两套并行 V2，打样链路会串版。
- **B21-05**：导出接口用裸 `dict` 且全表加载，既可 500 也可被大表拖垮内存；同类导出入口应统一走 schema + 分页/流式。

## 四、残余

- 本批测试是静态/最小替身断言，未在真实 PostgreSQL 上做并发写入；B23-05、B23-06 的并发后果是代码路径推断，B23-06 的单线程行为已由代码顺序直接证明。
- B24-01 未启动真实浏览器验证 CORS 预检，只按 Starlette 配置组合判定；默认配置不触发。
- 未改业务代码；B21–B24 的 24 条均未修复，等主人排期。
