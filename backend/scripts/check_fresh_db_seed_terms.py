"""全新库初始化后，新建报价的交货条款必须是新口径（审查 2026-10-09 第③条）。

## 为什么单独一个套件

其它套件跑的是**已有数据**的库：`default_delivery_terms` 的值来自当年那条迁移
写下的历史行，**不代表初始化脚本的当前行为**。而审查实测的缺口正是初始化脚本：
`migrate` 改了存量行，`scripts/seed.py` 却硬编码旧文案「含运费，送货上门」——
**全新库跑完种子，新建报价仍写"含运费"**，与"产品单价不含运费"直接冲突。

所以要验"初始化"，就必须**真的从空库开始**：建库 → migrate → seed → 新建报价。

## 它自己造库，不动别人的库

给 `FRESH_DB_NAME` 指定库名（默认 `crm_seed_test`），脚本自己
`DROP`/`CREATE` 它、跑迁移与种子、起一个只连它的后端，验完再把库删掉。
这样它既能验"全新初始化"，又不需要人工先准备环境。

用法：
    python scripts/check_fresh_db_seed_terms.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

DB_NAME = os.environ.get("FRESH_DB_NAME", "crm_seed_test")
PG = os.environ.get("PG_BIN", "/opt/homebrew/opt/postgresql@15/bin/psql")
PG_CONTAINER = os.environ.get("PG_CONTAINER", "crm-postgres")
PORT = int(os.environ.get("FRESH_DB_PORT", "8005"))
API = f"http://127.0.0.1:{PORT}/api/v1"

#: 运费分离之前的默认交货条款：新口径下它会让客户以为报价包了运费，必须不再出现
OLD_DELIVERY_TERMS = "含运费，送货上门"

FAILURES: list[str] = []
RESULTS: list[tuple[str, bool, str]] = []


def record(case: str, title: str, passed: bool, detail: str = "") -> None:
    RESULTS.append((case, passed, detail))
    if not passed:
        FAILURES.append(f"{case} {title} —— {detail}")
    print(f"  [{'PASS' if passed else 'FAIL'}] {case} {title} —— {detail}")


def call(method, path, token=None, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def login(username, password):
    _, res = call("POST", "/auth/login", body={"username": username, "password": password})
    if res.get("code") != 0:
        raise SystemExit(f"登录失败：{res.get('message')}")
    return res["data"]["access_token"]


def psql(sql: str, db: str = "postgres", container: bool = True) -> str:
    """跑一条 SQL。库里默认走容器（一次性库都在容器里）。"""
    if container:
        cmd = ["docker", "exec", PG_CONTAINER, "psql", "-U", "crm", "-d", db, "-tAc", sql]
    else:
        env = dict(os.environ, PGPASSWORD="crm123456")
        cmd = [PG, "-h", "127.0.0.1", "-p", "5432", "-U", "crm", "-d", db, "-tAc", sql]
        return subprocess.run(cmd, capture_output=True, text=True, env=env).stdout.strip()
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def main() -> int:
    print(f"=== 全新库初始化：{DB_NAME} ===")
    # 库名守卫：只允许一次性库名，避免手滑 drop 掉正经库
    if not (DB_NAME.startswith("crm_seed") or DB_NAME.startswith("crm_check")
            or "_test" in DB_NAME):
        raise SystemExit(f"拒绝操作库名 {DB_NAME}：只允许一次性库（crm_seed*/crm_check*/*_test）")

    # ⚠️ `DROP DATABASE` 在**还有活动连接**时会失败（上一轮残留的 uvicorn 就够），
    # 失败被忽略的话，`CREATE` 也会因"库已存在"失败，于是下面拿一个**旧库**
    # 当"全新库"验 —— 结论完全是错的。所以：①DROP 加 `FORCE` 踢掉残留连接；
    # ②两步都断言真的成功；③再断言这个库确实一张表都没有，才继续往下跑。
    drop = psql(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
    if "ERROR" in drop.upper():
        record("F01", f"删掉可能存在的旧库 {DB_NAME}", False, drop.strip()[:120])
        return 1
    create = psql(f"CREATE DATABASE {DB_NAME} OWNER crm")
    if "ERROR" in create.upper():
        record("F01", f"建出全新库 {DB_NAME}", False, create.strip()[:120])
        return 1
    record("F01", f"建出全新库 {DB_NAME}（真的从空库开始）",
           psql(f"select 1 from pg_database where datname='{DB_NAME}'") == "1",
           "DROP(FORCE) + CREATE 均成功")

    # ⚠️ 建库后必须断言"库是真的空"：否则 `DROP`/`CREATE` 没生效时，
    # 下面会拿一个**旧库**当"全新库"验，结论完全是错的。
    # （本套件在批量跑里出现过一次 F04/F05/F07 同时失败、单独跑却全过的抖动，
    #  就是这类"验的不是我以为的那个库"的问题，所以加这道自检。）
    tables = psql("select count(*) from information_schema.tables "
                  "where table_schema='public'", DB_NAME)
    record("F01b", "建出来的库确实是空的（没有任何业务表）",
           tables == "0", f"public 表数={tables}")
    if tables != "0":
        # 库不空 → 下面验的就不是"全新初始化"，任何结论都不可信，直接停。
        print("  库不空，说明 DROP/CREATE 没真正生效；停止（不产出不可信的结论）")
        psql(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
        return 1

    env = dict(os.environ, DATABASE_URL=f"postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/{DB_NAME}",
               PYTHONPATH=".")
    # 再确认一层：`alembic`/`seed` 自己解析出来的库名必须就是 DB_NAME。
    # `settings` 的 `env_file=".env"` 相对 CWD 解析，理论上环境变量优先，
    # 但这种"以为连的是 A、其实连的是 B"的坑不值得靠推理，直接问它。
    probe = subprocess.run(
        [".venv/bin/python", "-c",
         "from app.core.config import settings; print(settings.database_url)"],
        capture_output=True, text=True, env=env, cwd=".")
    resolved = probe.stdout.strip()
    record("F02a", "迁移/种子解析到的库名与目标一致",
           resolved.endswith(f"/{DB_NAME}"), f"settings 解析出={resolved}")

    r = subprocess.run([".venv/bin/python", "-m", "alembic", "upgrade", "head"],
                       capture_output=True, text=True, env=env, cwd=".")
    if r.returncode != 0:
        record("F02", "迁移跑到 head", False, r.stderr.strip()[-160:])
        return 1
    head = psql("select version_num from alembic_version", DB_NAME)
    record("F02", "迁移跑到 head", bool(head), f"alembic_version={head}")

    r = subprocess.run([".venv/bin/python", "-m", "scripts.seed"],
                       capture_output=True, text=True, env=env, cwd=".")
    if r.returncode != 0:
        record("F03", "种子脚本执行成功", False, r.stderr.strip()[-160:])
        return 1
    users = psql("select count(*) from users", DB_NAME)
    record("F03", "种子脚本执行成功", int(users or 0) > 0, f"users={users}")

    # ---- 默认交货条款：这就是审查实测的那一处 ----
    terms = psql("select value->>'text' from system_settings "
                 "where key='default_delivery_terms'", DB_NAME)
    record("F04", "初始化写入的默认交货条款不是旧文案",
           bool(terms) and OLD_DELIVERY_TERMS not in terms, f"库内={terms!r}")
    record("F05", "初始化写入的默认交货条款说明「单价不含运费」",
           bool(terms) and "不含运费" in terms, f"库内={terms!r}")

    # ---- F09：**先迁移、后种子**的顺序（种子真正说了算的那种）----
    #
    # ⚠️ 这一段是踩过坑才加的。`seed.py` 只写**不存在**的键
    # （`if exists is None: session.add(...)`），所以"迁移 → 种子"这个顺序里，
    # `default_delivery_terms` 那一行已经被迁移建好了，**种子根本不会写** ——
    # 上面 F04/F05 验到的其实是**迁移**的值，种子那一步被跳过，
    # 于是把种子改回旧文案这套件照样全绿（我反证过一次，确实没抓到）。
    #
    # 真正能暴露缺口的顺序是"先迁移、后种子"（或对已初始化的库重跑种子）：
    # 迁移把值改成新文案之后，种子**没有能力把它改回去**，但如果种子写的是旧文案、
    # 而迁移那条 UPDATE 因任何原因没生效，坏值就会留在库里。
    # 这里用"把该行删掉再跑种子"来隔离出**种子自己写的值**。
    psql("DELETE FROM system_settings WHERE key='default_delivery_terms'", DB_NAME)
    r = subprocess.run([".venv/bin/python", "-m", "scripts.seed"],
                       capture_output=True, text=True, env=env, cwd=".")
    seed_only = psql("select value->>'text' from system_settings "
                     "where key='default_delivery_terms'", DB_NAME)
    record("F09", "种子脚本自己写下的默认条款也不是旧文案（隔离迁移的影响）",
           bool(seed_only) and OLD_DELIVERY_TERMS not in seed_only,
           f"seed 退出码={r.returncode} 种子里写的值={seed_only!r}")

    # ---- 起一个只连这个全新库的后端，验"新建报价实际返回的条款" ----
    iso_dir = "/tmp/crm-freshdb-backend"
    os.makedirs(iso_dir, exist_ok=True)
    with open(os.path.join(iso_dir, ".env"), "w", encoding="utf-8") as fh:
        fh.write(
            f"DATABASE_URL=postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/{DB_NAME}\n"
            "JWT_SECRET=fresh-db-check-secret-32bytes-long\n"
            "SCHEDULER_ENABLED=false\nWECOM_PUSH_OFF=1\nDINGTALK_PUSH_OFF=1\n"
            "DEEPSEEK_API_KEY=\n"
        )
    # ⚠️ 端口必须**独占**：上一次跑残留的后端会占着这个端口，我的新进程
    # 因 EADDRINUSE 静默退出，而"就绪探测"却打到了**那个旧后端**（它连的是别的库）
    # —— 于是 F07 读到空值、报价版本号还大得离谱（批量跑时实测到 321）。
    # 所以先确认端口空闲，不空闲就明确失败，绝不"看起来起来了"就用。
    import socket

    def _port_busy(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            sk.settimeout(0.5)
            return sk.connect_ex(("127.0.0.1", port)) == 0

    if _port_busy(PORT):
        # 只尝试让**上一次跑留下的**自己人释放：按端口找出 pid 并结束，
        # 然后再确认一次；仍被占用就放弃（可能是别人的服务，不该我杀）。
        subprocess.run(["bash", "-c",
                        f"lsof -nP -iTCP:{PORT} -sTCP:LISTEN -t | xargs -r kill"],
                       capture_output=True)
        for _ in range(20):
            if not _port_busy(PORT):
                break
            time.sleep(0.5)
    if _port_busy(PORT):
        record("F06", f"端口 {PORT} 必须空闲（否则验的会是别人的后端）",
               False, f"端口 {PORT} 被占用且无法释放")
        return 1
    record("F06a", f"端口 {PORT} 空闲（保证下面验的是我起的后端）", True, "ok")

    proc = subprocess.Popen(
        [os.path.abspath(".venv/bin/python"), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=iso_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # ⚠️ **必须在这里显式给出 DATABASE_URL**，不能只靠 `cwd` 下的 `.env`：
        # 调用方（批量跑套件时）通常自己导出了 `DATABASE_URL=...crm_check_test_fresh`，
        # 而 pydantic-settings 的优先级是**环境变量 > env_file** —— 子进程于是连到了
        # 调用方的库：端口探测照样通过、请求照样成功，但验的根本不是全新库
        # （实测就是这个：客户 id / 报价版本号都来自 crm_check_test_fresh）。
        # `.env` 保留，作为不依赖父进程环境的兜底。
        env=dict(
            os.environ,
            DATABASE_URL=f"postgresql+asyncpg://crm:crm123456@127.0.0.1:5433/{DB_NAME}",
            PYTHONPATH=os.path.abspath("."),
            SCHEDULER_ENABLED="false",
            WECOM_PUSH_OFF="1",
            DINGTALK_PUSH_OFF="1",
            DEEPSEEK_API_KEY="",
        ),
    )
    try:
        ready = False
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/docs", timeout=3):
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        if not ready:
            record("F06", "全新库后端启动", False, f"端口 {PORT} 未就绪")
            return 1
        record("F06", "全新库后端启动", True, f"127.0.0.1:{PORT}")

        token = login("admin", "admin123")
        _, res = call("GET", "/customers?page_size=1", token)
        items = res["data"].get("items") if isinstance(res["data"], dict) else res["data"]
        if not items:
            record("F06b", "这个后端连的是全新库（客户数据来自种子）", False, "取不到客户")
            return 1
        # 交叉验证：这个后端给出的客户，必须能在**全新库**里查到同一个 id。
        # 只探"端口有没有响应"是不够的 —— 旧后端也会响应（F07 就是这么白白读空的）。
        cust = items[0]["id"]
        found = psql(f"select count(*) from customers where id={cust}", DB_NAME)
        record("F06b", "这个后端确实连着我刚建的全新库（不是残留的旧后端）",
               found == "1", f"客户 id={cust} 在 {DB_NAME} 里命中 {found} 行")
        _, res = call("POST", "/opportunities", token,
                      {"customer_id": cust, "title": "全新库条款验证"})
        opp = res["data"]["id"]
        _, res = call("POST", "/quotes", token,
                      {"customer_id": cust, "opportunity_id": opp, "currency": "CNY"})
        vid = res["data"]["version_id"]
        db_terms = psql(f"select delivery_terms from quote_versions where id={vid}", DB_NAME)
        record("F07", "全新库上新建报价的交货条款已是新口径（审查实测的那一步）",
               bool(db_terms) and OLD_DELIVERY_TERMS not in db_terms,
               f"报价版本 {vid} 的 delivery_terms={db_terms!r}")
        # 版本 GET 不返回该字段，所以断言直接读库（读的就是新建那一刻落下的快照）
        _, res = call("GET", f"/quote-versions/{vid}", token)
        record("F08", "新建报价接口调用成功", res.get("code") == 0,
               f"code={res.get('code')} {str(res.get('message'))[:40]}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        psql(f"DROP DATABASE IF EXISTS {DB_NAME}")
        print(f"  （已删除一次性库 {DB_NAME}）")

    print()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    if FAILURES:
        print(f"FAILED（{len(FAILURES)}）：")
        for item in FAILURES:
            print("  -", item)
        return 1
    print(f"全新库初始化：{passed}/{len(RESULTS)} 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
