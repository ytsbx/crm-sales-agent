# 审查复现证据

基线及缺陷说明见 [审查报告](/Users/zhaorongkai/Desktop/CRM-Sales-Agent-V1.1/审查报告-2026-10-12.md)。所有数据、人员名称和金额均来自一次性库的种子或审查夹具，未连接生产/演示库验证。

- `crm_audit_probes.py`：真实业务函数验证折扣审批、含运费的硬底、外币空值、转单舍入及两个会话的核定覆盖。
- `crm_audit_agent_probe.py`：停用 SKU 导致 Agent 创建失败，但报价已持久化。
- `crm_audit_pdf_probe.py`：主数据和运费未确认的已审批版本仍下载正式 PDF。
- `crm_audit_debug_probe.py`：默认关闭 DEBUG 时 `/docs` 为 404。
- `crm_audit_local_image_probe.py`：只创建和读取本次临时 PNG，确认未经转义的客户名称能让 PDF 读取 FILE_ROOT 外图片。
- `crm_audit_lan_probe.mjs`：独立浏览器访问本机局域网 HTTP 页面，确认 UUID API 不可用，20 秒超时后退出。
- `crm_audit_checks.log`：104 个接口套件及 417 个单元测试完整通过。
- `crm_audit_ui.log`：干净隔离库 UI 冒烟完整通过。截图位于 `/tmp/crm_audit_ui`。

数据库反例脚本必须通过 `crm_audit_20261012.py` 启动，主反例模块还会执行项目隔离库/接口防呆。驱动固定使用 `crm_check_test_audit_20261012`、本机 API 8069，并关闭外部集成及模型。需要本机 PostgreSQL 和当前项目依赖；测试库已存在时 `init` 会拒绝创建。

在仓库根目录重现核心反例（先在单独终端启动隔离后端）：

```bash
AUDIT_DIR="$(pwd)/审查证据-2026-10-12"
backend/.venv/bin/python "$AUDIT_DIR/crm_audit_20261012.py" init
backend/.venv/bin/python "$AUDIT_DIR/crm_audit_20261012.py" serve
```

另一个终端：

```bash
AUDIT_DIR="$(pwd)/审查证据-2026-10-12"
backend/.venv/bin/python "$AUDIT_DIR/crm_audit_20261012.py" run "$AUDIT_DIR/crm_audit_probes.py"
backend/.venv/bin/python "$AUDIT_DIR/crm_audit_20261012.py" run "$AUDIT_DIR/crm_audit_agent_probe.py"
backend/.venv/bin/python "$AUDIT_DIR/crm_audit_20261012.py" run "$AUDIT_DIR/crm_audit_pdf_probe.py"
```

Agent 反例特意保留“失败仍提交”的夹具来证明事务缺陷，所以后续 UI 冒烟应重建隔离库后再运行，不应沿用该夹具库。结束后先停止自己启动的后端，再执行驱动 `cleanup`，只删除这一个固定的一次性库。

额外离线/浏览器结果：

```text
DEBUG_DEFAULT False
CI_STARTUP_PROBE 404
LAN_BROWSER {"secure":false,"randomUUID":"undefined","error":"crypto.randomUUID is not a function"}
PDF_LOCAL_IMAGE {'source_outside_file_root': True, 'image_embedded': True, 'pdf_bytes': 3976}
```
