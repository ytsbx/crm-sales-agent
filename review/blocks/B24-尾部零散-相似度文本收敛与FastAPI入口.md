# B24 尾部零散：相似度 / 文本收敛 / FastAPI 入口

> 审查范围：`backend/app/core/similarity.py` 133 行、`core/textfit.py` 45 行、`modules/__init__.py` 1 行、`backend/app/main.py` 194 行，合计 373 行。
>
> 审查方式：只读业务代码，只写 `review/`；未运行测试，未改代码。本块是全局工具和路由注册入口，不直接处理金额；没有 P0/P1/P2。

## 一、本块结论

| 严重度 | 数量 | 编号 |
|---|---|---|
| P0 | 0 | — |
| P1 | 0 | — |
| P2 | 0 | — |
| P3 | 3 | B24-01、B24-02、B24-03 |
| 待确认 | 0 | — |

一句话：**`similarity.py` 的查重打分和 `textfit.py` 的列宽收敛没有发现会直接造成金额/权限问题的缺陷；`main.py` 的现有路由顺序也按注释核对了静态/动态路径。剩下的都是低风险：CORS 通配符与凭据组合未校验、健康检查吞 DB 异常不打日志、查重只清普通空格不清制表符/换行导致同名漏判。**

## 二、逐文件检查记录

### `backend/app/core/similarity.py`（133 行，已读完）

我检查了什么：公司名归一化、最长公共子串比例、加权打分、配置权重读取、分数上限、查重原因文案。

结果：

- `normalize_name` 已按最长后缀优先、只削结尾、至少保留 1 个字符；不会再把“上海厂务贸易”中间的“厂”误删，这是 2026-10-14 已修的问题，当前实现正确。
- `longest_common_substring_ratio` 对空串返回 0，包含关系返回 1；对 200 字以内的客户名做 O(n*m) 动态规划，量级可接受。
- `similarity_score` 的分数最终 `min(score, 100)`，但**没有下限**；权重来自 `settings_service.get_setting("dedup_scoring")`，设置侧已校验“非负整数”，唯一现有调用方 `contact_util.py:401-416` 会把设置值传进来，因此当前路径安全。若未来有调用方直接传负权重/非整数，分数可能为负或 `int()` 截断，属于防御性问题，未单列编号。
- **发现 B24-03**：`normalize_name` 只 `.replace(" ", "")` 和全角空格，不处理 `\t`、`\n`、`\r`。客户名 schema 只限长度、不禁止制表符/换行，带这些字符的名称与正常空格名称不会归一成同一个串，查重可能漏掉真实重复。
- `CUSTOMER_SCORE_FALLBACKS` 与 `settings/service.py:134-137` 的默认值不完全一致（例如 `weight_name_exact` fallback 60，设置默认 70；`weight_name_contains` fallback 45，默认 55）。`similarity_score` 只在调用方不传权重时用 fallback；设置读不到时用设置默认；两套口径会让“配置缺失/读取失败”时的分数与正常配置不同。属于 P3 整洁度，未造成当前 bug。

### `backend/app/core/textfit.py`（45 行，已读完）

我检查了什么：`fit_column` 的前缀/后缀预算、空文本、超长截断、省略号、极端 `keep <= 0` 路径。

结果：`limit`、`prefix`、`suffix` 的字符预算计算正确；`keep <= 0` 时把前缀+正文+后缀整体截到 `limit`，不会越界；`keep == 1` 时用省略号占位；正常路径截断后长度恰好等于 `limit`。未发现越界/金额/权限问题。调用方 `quote/service.py:1347` 用 `fit_column(spec_snapshot or inquiry.description, 200)` 把用户需求描述收进 `String(200)`，原始 `custom_inquiries.description` 仍是完整 Text，属于“快照列宽收敛”而非全量数据丢失，且注释已说明这是为避免 PG 500；不报为 bug。

### `backend/app/modules/__init__.py`（1 行，已读完）

只有模块说明 docstring，无代码、无导入、无副作用，未发现任何问题。

### `backend/app/main.py`（194 行，已读完）

我检查了什么：FastAPI 创建、lifespan 启停顺序、CORS、异常处理器、全部 router 注册顺序、`/health` 探活。

结果：

- 路由顺序按注释核对了主要静态/动态冲突：`customer_io_router` → `customer_tags_router` → `contacts_router` → `customer_router`；`product_io_router` → `product_router`；`pricing_io_router` → `pricing_router`；`lead_io_router` → `lead_router`；`contract_customer_router` → `contract_router`。没有发现当前路径被动态路由抢先匹配。
- `lifespan` 的 `oa_stream.stop` → `stop_scheduler` → `reset_adapters` → `reset_client` 是嵌套 finally，前一步失败不会跳过后面的清理；`start_scheduler`/`oa_stream.start` 在 `yield` 前，异常会阻止应用启动，符合预期。
- `docs_url` 只在 `debug` 打开，生产默认关闭；`httpx/httpcore` 日志降到 WARNING，避免钉钉 access_token 进请求日志；没有把完整请求 URL 打进普通日志。
- **发现 B24-01**：`allow_origins=settings.cors_origin_list`、`allow_credentials=True`，但 `cors_origins` 只按逗号拆分，不校验 `*`；如果生产环境把 `CORS_ORIGINS` 配成 `*`，与凭据模式组合不符合 CORS 规范，浏览器会拒绝带凭据请求（或行为依 Starlette 版本），表现为前端登录后跨域请求失败。默认值是 localhost 显式列表，当前不触发。
- **发现 B24-02**：`/health` 的 DB 检查 `except Exception: db_ok = False` 没有 `logger.exception`，也没有连接/查询超时；数据库故障时只返回 `database=error`，运维日志里没有原因，健康检查本身还可能被慢 DB 拖住。
- `/health` 无鉴权是探活设计，测试 `test_backend_review_followups.py:107-120` 明确断言无 token 也能拿到 `status=up`；不报为越权。
- 没有 root/health 之外的全局中间件；CORS 是唯一全局安全相关配置。

## 三、发现清单

### B24-01 P3 CORS 允许来源配置为 `*` 时未与 `allow_credentials=True` 互斥校验

**位置**：`backend/app/main.py:102-108`；`backend/app/core/config.py:44、209-210`

**触发条件**：生产环境把 `CORS_ORIGINS` 配成 `*`（或包含 `*` 的列表），应用启动。

**后果**：`CORSMiddleware` 同时收到通配来源和 `allow_credentials=True`。这不符合 CORS 规范：浏览器不会把 `Access-Control-Allow-Origin: *` 与凭据请求一起接受，具体行为依 Starlette 版本可能是拒绝带 Cookie/Authorization 的跨域请求，或回显请求 Origin。前端会出现“接口 200 但浏览器拦住/登录态丢失”，排查时容易误判成后端权限问题。默认显式 localhost 不触发。

**证据代码**
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
```python
cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

@property
def cors_origin_list(self) -> list[str]:
    return [x.strip() for x in self.cors_origins.split(",") if x.strip()]
```

**建议**：启动时校验 `cors_origin_list`：含 `*` 且 `allow_credentials=True` 直接拒绝启动或降级 `allow_credentials=False`；生产环境要求显式域名列表。可以加一条配置校验测试。

**测试覆盖**：未覆盖。`backend/tests/test_backend_review_followups.py:107-121` 只测 `/health` 和 OA health 权限，没有 CORS 预检测试。

### B24-02 P3 健康检查吞掉数据库异常且不打日志，慢 DB 还可能拖住探活

**位置**：`backend/app/main.py:171-194`

**触发条件**：数据库连接失败、查询超时、连接池耗尽，或 DB 响应很慢。

**后果**：`except Exception: db_ok = False` 只把响应改成 `database=error`，没有记录异常类型/堆栈；运维只能看到“degraded”，无法从应用日志判断是连接拒绝、认证失败还是慢查询。`SessionLocal()` 的 `execute("SELECT 1")` 没有显式超时，DB 挂起时 `/health` 可能一起挂住，负载均衡的探活会超时。测试只验证“HTTP 200 + database=error 被判未就绪”，不验证日志/超时。

**证据代码**
```python
db_ok = True
try:
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
except Exception:
    db_ok = False
return ok({"status": "up" if db_ok else "degraded", ..., "database": "ok" if db_ok else "error"})
```

**建议**：`except Exception` 里 `logger.exception("health db check failed")`（不要返回细节给探活响应）；给 `SELECT 1` 加短超时（如 2–3 秒，按驱动能力）；保留当前 200+degraded 的探活契约。

**测试覆盖**：部分覆盖。`backend/tests/test_audit_risk_repairs.py:52-63` 覆盖 `wait_backend.is_ready` 对 `database=error` 的判定；`test_backend_review_followups.py:107-120` 覆盖 endpoint 正常返回。没有 DB 异常日志/超时用例。

### B24-03 P3 公司名归一化只清普通空格，制表符/换行会让同名客户漏判

**位置**：`backend/app/core/similarity.py:58`

**触发条件**：客户名从 Excel/ERP/手工输入带入 `\t`、`\n`、`\r` 或其它空白字符，例如 `"宁波\t宏远"` 与 `"宁波 宏远"`。

**后果**：`normalize_name` 只去掉 `" "` 和全角空格；带制表符/换行的名称归一化结果不同，`left_name == right_name` 不成立，`weight_name_exact` 拿不到，可能把同一家公司判成不同客户。客户名 schema 只限制 1–200 字，不禁止这些字符，导入路径可能带进来。不会直接影响金额，但会影响撞单裁定/合并选择。

**证据代码**
```python
text = (name or "").strip().replace(" ", "").replace("　", "")
```
```python
class CustomerCreate(CreateModel):
    name: str = Field(min_length=1, max_length=200, ...)
```

**建议**：用 `re.sub(r"\s+", "", text)` 或至少同时去掉 `\t\r\n`；如果业务要求保留名称内部空格，至少把所有 Unicode 空白归一成普通空格后再比较。补 `normalize_name("A\tB") == normalize_name("A B")` 的单测。

**测试覆盖**：未覆盖。`backend/tests/test_customer_dedup_scope.py` 只测查重的数据范围/手机号来源，没有名称归一化边界用例。

## 四、补充维度核对

- **金额 Decimal**：本块 4 个文件没有金额字段/金额计算；`similarity_score` 的分数是查重分，不是钱。
- **负数/0/超大值/小数位**：`similarity_score` 的权重若从设置读，设置侧已保证非负整数、阈值 0–100；纯函数本身没有下界，属于 B24 说明里的防御性问题。`textfit` 的 `limit` 为列宽常量，未做运行时校验。
- **事务边界**：本块无 DB 写入；`main.py` 的 lifespan 只负责启停，不提交事务；`/health` 只读 `SELECT 1`。
- **异常是否吞掉/泄露**：`/health` 的 `except Exception` 吞掉异常且不打日志（B24-02）；不向前端返回 DB 细节，这一点是好的。
- **审计**：本块无收款/改价/作废操作，不涉及审计。
- **测试覆盖**：B24-01/02/03 均未覆盖或仅部分覆盖；本块未运行测试。相关已读用例：`test_audit_risk_repairs.py:52-63`（health readiness）、`test_backend_review_followups.py:107-120`（health endpoint）、`test_customer_dedup_scope.py`（查重范围/手机号）。没有 `similarity.py`/`textfit.py` 的直接单测。

## 五、本块没有看的部分及原因

- **未运行测试**：所有“覆盖/未覆盖”是阅读 `backend/tests` 后的判断，不是运行结果。
- **未逐个枚举 50 个 router 的全部路径**：只按 `main.py` 注释核对了 io/static 与动态路径的主要顺序，以及 sample/order/contract 等几个高风险前缀；不能保证未来新增静态路由不会被先注册的动态路由 shadow。
- **未实测 CORS**：没有起浏览器/Starlette 预检请求验证 `CORS_ORIGINS=*` 的实际响应头，B24-01 是按配置组合和 CORS 规范判断。
- **未审 `similarity.py` 的全部调用方**：只读了 `contact_util.py:330-420` 的调用段；客户查重/联系人查重的完整业务流程属 B13，本块不重复。
- **未审 `textfit.py` 的全部调用方**：只 spot-read 了 `quote/service.py:1347`、`contract/service.py`、`bizdoc/service.py` 的调用点；其他调用方是否都传字符串未逐一核对。
- **未审前端**：CORS 失败/健康状态展示在 F 系列；本块未看。

## 六、结论

本块没有 P0/P1/P2。B24-01/B24-02 是入口/运维配置的 P3；B24-03 是查重归一化的 P3。`textfit.py` 未发现越界或数据丢失问题，`modules/__init__.py` 为空文件无问题。台账 B24 已标“已审”，下一块进入 F 系列前端或按台账继续。
