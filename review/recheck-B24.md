# B24 复核（专门证伪）

- 复核对象：`review/blocks/B24-尾部零散-相似度文本收敛与FastAPI入口.md`
- 环境：FastAPI 0.141.1 / Starlette 1.7.0；Python 3.12（`backend/.venv`）
- 方法：重读 `main.py`、`config.py`、`database.py`、`similarity.py`、`textfit.py`、`contact_util.py`、`settings/service.py`；能落的用 `review/repro/test_b24_backend.py` 跑；不连数据库、不改业务代码。
- 复现：`cd backend && PYTHONPATH=. .venv/bin/python -m pytest ../review/repro/test_b24_backend.py -q`
- 结果：11 条断言失败（断言写的是“修复后应有的行为”），1 条通过。

## 结论表

| 编号 | 结论 | 关键证据 |
|---|---|---|
| B24-01 | **确认配置缺口，但审查员写的后果是误报** | `*` + `allow_credentials=True` 时服务端把任意 Origin 回显成 ACAO，并回 `ACAC: true`；不是“浏览器拒绝/登录失败” |
| B24-02 | **确认** | DB 异常无任何日志；连接池 `pool_timeout=30s`，`SELECT 1` 无查询超时 |
| B24-03 | **确认（仅内部空白）** | `strip()` 已去首尾；内部 `\t`/`\n`/`\r\n` 仍不归一，`宁波\t宏远` ≠ `宁波宏远` |
| 路由顺序 | 审查员结论成立 | 递归展开 610 条路由，静态路径被前面的动态路径抢占：0 条 |

## 逐条

### B24-01｜确认配置缺口；后果判断误报

代码：`main.py:102-108`、`config.py:44、209-210`。`cors_origin_list` 只按逗号拆分，不拒绝 `*`；`allow_credentials=True` 写死。

当前仓库的 `.env`、`.env.development.local`、`.env.oa-local` 都是显式来源列表，**现在不触发**；只有生产把 `CORS_ORIGINS` 配成 `*` 才会进入下面的路径。配置层没有任何互斥校验，也没有 CORS 预检测试兜底。

实际复现（`TestClient`，`Origin: https://evil.example`）：

- 普通请求：`ACAO='https://evil.example'`、`ACAC='true'`
- 预检 `OPTIONS`：`200`、`ACAO='https://evil.example'`
- `Settings(cors_origins="*")` 不抛错

Starlette 1.7 的逻辑是：先写 `Access-Control-Allow-Origin: *`，再因 `allow_credentials=True` 用请求 Origin 覆盖。因此审查员说的“浏览器拒绝带凭据请求、前端登录失败”在当前依赖版本不成立；真实后果是**任意来源被回显并允许凭据**。

这应归为安全配置问题，而不是 P3 登录体验问题。现状鉴权是 `Authorization: Bearer`（`auth/router.py`），没有 Cookie，恶意站点拿不到 localStorage 里的 token，实际利用面有限；但一旦引入 Cookie 会话或同域 Cookie，就是 P1 级跨源读取风险。建议启动时拒绝 `*` + credentials，或强制 `allow_credentials=False`。

### B24-02｜确认

代码：`main.py:182-187`、`database.py:10-16`。

- 把 `SessionLocal` 换成必抛异常的假会话后调用 `health()`：响应 `{'status':'degraded','database':'error'}`，`caplog` 捕获到 **空字符串**，没有任何异常日志。
- `engine.pool._timeout = 30.0`；`create_async_engine` 未传 `connect_args`/`command_timeout`。DB 连接池耗尽或查询挂起时，`/health` 最长可被拖 30s（查询本身还可能更久）。
- 审查员说的“无日志、无短超时”成立。

补充：`/health` 即使 `database=error` 也返回 HTTP 200。仓库自己的 `wait_backend.is_ready` 会解析 body，但普通负载均衡只看状态码时会把“DB 已挂”判成健康。审查员没有提这一点，见下文“漏项”的次要项。

### B24-03｜确认，但范围要收窄

代码：`similarity.py:58`、`customer/schema.py:11-12`。

- `strip()` 会去掉首尾的 `\t/\n/\r`，所以只有**名称内部**的控制空白会漏；审查员举的 `"宁波\t宏远"` 正是内部，成立。
- `CustomerCreate(name="宁波\t宏远")` 通过 schema，内部制表符可入库。
- 归一化结果：`'宁波\t宏远'` 仍是 `'宁波\t宏远'`，而 `'宁波 宏远'` 是 `'宁波宏远'`，不相等。

严重度 P3 合理：只影响“疑似重复”候选是否出现，不自动合并，也不会直接改钱。

### 路由顺序｜审查员结论成立

按 `app.routes` 递归展开 `_IncludedRouter` 的所有 APIRoute（含 prefix），共 610 条。对“前面是动态段、后面同位置是字面量、方法有交集”的组合做扫描：**0 条**。没有发现当前路由被 shadow 的反例。

## 漏项：审查员漏掉的最大风险

**公司名归一化只处理空格，没有做 Unicode 兼容归一（NFKC）和标点归一；同一家公司在常见写法差异下会拿到 0 分，低于阈值 50，根本不会进入“疑似重复”候选。**

审查员只写了 `\t/\n`，但更常见的是全角/半角括号、全角字母、间隔点：

| 名称 A | 名称 B | 归一化后 | 查重分（默认权重） |
|---|---|---|---|
| 宁波宏远（集团）有限公司 | 宁波宏远(集团)有限公司 | `宁波宏远（集团）` / `宁波宏远(集团)` | **0** |
| ＮＢＡ贸易有限公司 | NBA贸易有限公司 | `ｎｂａ贸易` / `nba贸易` | **0** |
| 宁波·宏远包装有限公司 | 宁波宏远包装有限公司 | `宁波·宏远包装` / `宁波宏远包装` | **36**（阈值 50，仍不提示） |

后果：同一客户被当成两家新开档，订单、应收、回款、跟进历史分到两条客户记录上；后续对账、业绩统计、撞单裁定都会出现“同一家公司在系统里有两份”的麻烦。它比 B24-03 的制表符更常见，也更难被业务发现（页面不会报错，只是“没提示重复”）。

建议：`normalize_name` 先做 `unicodedata.normalize("NFKC", text)`，再去掉 `（）()·•、,，.。` 等标点，最后做后缀裁剪与空白压缩；补 `宁波宏远（集团）有限公司 == 宁波宏远(集团)有限公司` 的单测。

次要漏项：`/health` 的 `200 + degraded` 对只认状态码的探活等于“永远健康”；如果生产用 k8s/ELB 的 HTTP 探针，DB 挂了仍会被留在流量里。建议区分 `/health`（存活，200）与 `/ready`（就绪，DB 失败返回 503），或让探针解析 body。

## 残余风险

- `normalize_name` 的“削到只剩 1 字”规则会把两字名称塌成一字：`五金厂` 与 `五金` 都得 `五金`，默认权重下 70 分“企业名称一致”。是否算误报取决于业务是否认可这种简称；当前会直接把两条推到疑似重复里，需人工确认。
- 以上测试均为纯函数/内存 HTTP，未连真实数据库，未验证连接池耗尽、慢查询的真实耗时。

## 复现文件

- `review/repro/test_b24_backend.py`：CORS、health 日志/超时、名称归一化、漏项评分。
- 运行输出：`11 failed, 1 passed`（通过的 1 条是 `CustomerCreate` 接受制表符，用来证明触发条件可达）。
