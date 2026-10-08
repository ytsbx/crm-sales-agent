"""案例修订稿生命周期（第四批 §5.1.5 + 返工单第 6 条 / 第 7 条，2026-10-06）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

## 这条修的是什么

返工单第 6 条：**一开修订稿，已发布的案例就从列表里消失**。三个具体缺陷：

1. 列表按"存在指向自己的修订稿"排除原版 —— 于是作者刚点完"修订"（只生成一份
   还没发布的草稿），原版立刻从列表里没了，看着像案例丢了。
   判据应该是"**自己已被取代**"：修订稿没发布，原版就是当前版本。
2. 详情要求 `status == published`，被取代的旧版（`superseded`）直接 403。
   旧版是培训资料，必须**读得到**（只是不能再改）。
3. 同一原版能反复开修订稿，建出一排同版本草稿，谁也说不清哪份是正主。

返工单第 7 条那半（前端接通修订入口、驳回意见不再写死）属于界面，回归覆盖不到；
这里覆盖的是**接口层**：修订稿的生成、幂等、发布后原版转 `superseded`、旧版仍可读。

跑法（需要后端在跑）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \\
      PYTHONPATH=. .venv/bin/python scripts/check_case_revisions.py
"""

import asyncio
import os
import time
from urllib.parse import urlparse

from sqlalchemy import text
from _test_support import require_isolated_db

require_isolated_db()

from app.core.config import settings
from app.core.database import SessionLocal
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKREV{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: object, expected: object = True) -> None:
    ok = condition == expected
    print(f'  {"OK  " if ok else "FAIL"} {label}：{condition!r}' + ("" if ok else f"（应为 {expected!r}）"))
    if not ok:
        FAILURES.append(label)


def check_true(label: str, condition: bool, detail: str = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}' + (f"：{detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


def case_states(token: str, *, include_history: bool = False) -> dict[int, str]:
    """本套件造的那几条案例的 {id: 状态}，走真实列表接口。

    用标题关键词把范围限制在本套件自己的夹具上，避免读到库里别的数据。
    """
    query = f"/cases?keyword={MARKER}&page_size=50"
    if include_history:
        query += "&include_history=true"
    status, res = call("GET", query, token=token)
    assert res.get("code") == 0, res
    return {row["id"]: row["status"] for row in res["data"]["items"]}


async def main():
    import app.main
    _ = app.main

    assert urlparse(BASE).hostname in {"127.0.0.1", "localhost", "::1"}, BASE
    db = urlparse(settings.database_url)
    assert db.hostname in {"127.0.0.1", "localhost", "::1"}
    assert "test" in db.path.lower() or os.getenv("CI") == "true", db
    assert settings.wecom_push_off and settings.dingtalk_push_off and not settings.scheduler_enabled, (
        "这条回归只能在推送全关的隔离库跑"
    )

    zhangsan = login("zhangsan", "123456")   # 作者（销售）
    lisi = login("lisi", "123456")           # 主管（能审核）
    wangwu = login("wangwu", "123456")       # 非作者、非主管：普通读者视角

    try:
        print("=== 0. 先做一条已发布的案例 ===")
        status, res = call(
            "POST",
            "/cases",
            token=zhangsan,
            body={
                "title": f"{MARKER} 修订生命周期案例",
                "customer_label": "某机械厂",
                "industry": "机械",
                "stage_reached": "repeat",
                "key_actions": "先出样再锁产能",
                "lessons": "把交期写成书面承诺",
            },
        )
        check("建案例草稿", res.get("code"), 0)
        v1_id = res["data"]["id"]
        call("POST", f"/cases/{v1_id}/submit", token=zhangsan, body={})
        status, res = call(
            "POST", f"/cases/{v1_id}/review", token=lisi,
            body={"approve": True, "note": "做法可复制，通过"},
        )
        check("主管审核发布", res.get("code"), 0)
        check("第一版是已发布", res["data"]["status"], "published")
        check("第一版版本号是 1", res["data"]["version"], 1)

        print("=== 1. 开修订稿：原版**不能**从列表消失（返工单第 6 条的核心）===")
        status, res = call("POST", f"/cases/{v1_id}/revise", token=zhangsan)
        check("开修订稿成功", res.get("code"), 0)
        v2_id = res["data"]["id"]
        check("修订稿是草稿", res["data"]["status"], "draft")
        check("修订稿版本号 +1", res["data"]["version"], 2)
        check("修订稿指向原版", res["data"]["revision_of_id"], v1_id)

        default_states = case_states(zhangsan)
        check("草稿阶段：原版**仍在**默认列表里，而且仍是已发布",
              default_states.get(v1_id), "published")
        check("草稿阶段：修订稿也在列表里（作者看得到自己的草稿）",
              default_states.get(v2_id), "draft")

        print("=== 2. 重复开修订稿：最多一份在途（幂等）===")
        status, res = call("POST", f"/cases/{v1_id}/revise", token=zhangsan)
        check("重复开不报错", res.get("code"), 0)
        check("重复开返回的是**同一份**在途修订稿", res["data"]["id"], v2_id)
        all_states = case_states(zhangsan, include_history=True)
        drafts = [
            cid for cid, st in all_states.items() if st == "draft" and cid != v2_id
        ]
        check("没有多出第二份草稿", len(drafts), 0)

        print("=== 3. 修订稿发布：原版转「已被取代」并从默认列表退场 ===")
        call("POST", f"/cases/{v2_id}/submit", token=zhangsan, body={})
        status, res = call(
            "POST", f"/cases/{v2_id}/review", token=lisi,
            body={"approve": True, "note": "新版更完整，通过"},
        )
        check("修订稿发布成功", res.get("code"), 0)
        check("修订稿变成已发布", res["data"]["status"], "published")

        status, res = call("GET", f"/cases/{v1_id}", token=zhangsan)
        check("原版转为「已被取代」", res["data"]["status"], "superseded")
        check("原版记着被谁取代了", res["data"]["superseded_by"], v2_id)

        reader_states = case_states(wangwu)
        check("默认列表只列当前版本：原版退场", v1_id in reader_states, False)
        check("默认列表列的是新版", reader_states.get(v2_id), "published")

        print("=== 4. 旧版仍然**读得到**（只读），这是「不断档」的关键 ===")
        status, res = call("GET", f"/cases/{v1_id}", token=wangwu)
        check("普通读者打开旧版详情不再是 403", status, 200)
        check("读到的确实是那一版内容", res["data"]["version"], 1)
        check("读者视角看到它已被取代", res["data"]["superseded_by"], v2_id)

        history_states = case_states(wangwu, include_history=True)
        check("带 include_history 时旧版列得出来（历史版本可查）",
              history_states.get(v1_id), "superseded")

        print("=== 5. 旧版只读：谁也改不动 ===")
        status, res = call(
            "PATCH", f"/cases/{v1_id}", token=zhangsan, body={"title": f"{MARKER} 偷改旧版"}
        )
        check("作者改旧版被拒（要改就开新修订稿）", status, 422)
        status, res = call(
            "PATCH", f"/cases/{v1_id}", token=lisi, body={"title": f"{MARKER} 主管偷改旧版"}
        )
        check("主管也不能原地改已发布的版本", status, 422)

        print("=== 5.1 **不能**从旧版开修订稿（实测确认过的坑）===")
        # 从 superseded 的 V1 开出来的草稿取的是 V1 的旧内容，而且版本号会跟已发布的
        # V2 撞号；一旦审核通过就是"用旧内容把改进过的版本覆盖掉"——内容倒退。
        # 前端也不给这个入口，这里是接口层的兜底。
        status, res = call("POST", f"/cases/{v1_id}/revise", token=zhangsan)
        check("从已被取代的旧版开修订稿被拒", status, 422)
        check("并且说明了该去哪儿开（基于当前发布版）",
              "当前发布版" in str(res.get("message")), True)
        status, res = call("POST", f"/cases/{v1_id}/revise", token=lisi)
        check("主管也不行（这条不是权限问题，是口径）", status, 422)
        # 反过来：当前发布版（V2）仍然开得了，而且版本号是 3，不撞号
        status, res = call("POST", f"/cases/{v2_id}/revise", token=zhangsan)
        check("从当前发布版开修订稿仍然正常", status, 200)
        check("新草稿版本号 = 3（与已有的 V2 不撞号）", res["data"]["version"], 3)
        check("新草稿基于当前发布版（而不是更早的那一版）",
              res["data"]["revision_of_id"], v2_id)

        print("=== 6. 修订稿的修订稿：内容承接当前版本，不是最早那一版 ===")
        v3_id = res["data"]["id"]
        call("PATCH", f"/cases/{v3_id}", token=zhangsan,
             body={"title": f"{MARKER} 第三版"})
        call("POST", f"/cases/{v3_id}/submit", token=zhangsan, body={})
        status, res = call("POST", f"/cases/{v3_id}/review", token=lisi,
                           body={"approve": True, "note": "第三版通过"})
        check("第三版发布成功", res.get("code"), 0)
        # V2 被取代、V1 仍是 superseded（链条完整）
        status, r2 = call("GET", f"/cases/{v2_id}", token=zhangsan)
        check("V2 转为已被取代", r2["data"]["status"], "superseded")
        check("V2 指向新的取代者 V3", r2["data"]["superseded_by"], v3_id)
        # 三条默认列表里只剩最新的那一版
        final_states = case_states(wangwu)
        check("默认列表只剩最新一版", sorted(final_states.keys()), [v3_id])
        hist = case_states(wangwu, include_history=True)
        check("两版旧版都还查得到（不断档）",
              sorted(hid for hid, st in hist.items() if st == "superseded"),
              sorted([v1_id, v2_id]))

        # 把 v3 也记进清理范围（finally 是按标题前缀删的，这里只是留个引用）
        _ = v3_id

    finally:
        async with SessionLocal() as s:
            # 案例的表层关系只有 revision_of_id 一列（不是子表），按标题前缀物理删即可。
            # 顺序：先删修订稿再删原版，避免自引用外键（若有）挡住。
            await s.execute(
                text("delete from sales_cases where title like :m"), {"m": f"%{MARKER}%"}
            )
            await s.execute(
                text(
                    "delete from audit_logs "
                    "where business_type = 'case' and after_data::text like :m"
                ),
                {"m": f"%{MARKER}%"},
            )
            await s.commit()

    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)} 项：" + "、".join(FAILURES))
        raise SystemExit(1)
    print("OK 案例修订：开修订稿不丢原版、幂等、发布后旧版仍可读且只读")


if __name__ == "__main__":
    asyncio.run(main())
