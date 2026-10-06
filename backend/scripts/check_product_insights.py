"""新品洞察：评审冻结、转换与权限连续性（第五批，2026-10-06）。

**只在隔离库跑**：库名必须含 test（或 CI=true），且推送开关全关。

覆盖第五批的七处已确认问题：

1. **内容冻结与评审轮次**：待评审期间不能改关键内容；已通过后改了会退回重审并加一轮。
   修复前"已通过"之后随便改，状态仍是"已通过"——批准的根本不是同一份内容。
2. **字段校验与清空**：全空白标题被拒、负数价格被拒、传 null 能真正清空。
   修复前 `if value is not None` 会把传进来的 null 跳过，界面上清空了、库里旧值还在。
3. **转换走统一流程**：转出来的需求带 `inquiry_no`（修复前是裸的一条记录，
   报价/打样没法靠编号指回它，溯源断了）。
4. **幂等**：同一个 `request_key` 重试不会建出第二条需求。
5. **权限连续性**：没客户的洞察转成「内部开发需求」，**不因"没客户"而人人可见**。
   修复前 `apply_scope` 对 `customer_id IS NULL` 一律放行。
6. **删除保护**：已转需求的洞察不能删（删了来源追溯就断了）。
7. **评审人按权限码**：有 `product:review` 的能评、没有的不能。
   修复前写死"主管/管理员"角色。

角色分工（**这是本套件最容易写错的地方**）：
- `zhaoliu` 产品/开发评审人：有 `product:manage` + `product:review`，**数据范围是 self**；
- `admin` 全权；`zhangsan` 业务员（只有 `product:view`，既不能建也不能评）；
- `wangwu` 财务（无 `product:review`）。
洞察本来就不是业务员建的（建/改/转都要 `product:manage`），第一版套件按"业务员建"写，
第一步就吃了 403。

跑法（隔离库；不要对着默认开发库跑，`check_*` 会清库）：
    cd backend
    API_BASE=http://127.0.0.1:8001/api/v1 DATABASE_URL=...crm_sales_agent_test \
      PYTHONPATH=. .venv/bin/python scripts/check_product_insights.py
"""

import asyncio
import os
import time
from urllib.parse import urlparse

from sqlalchemy import String, delete, select

from app.core.audit import AuditLog
from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.inquiry.model import CustomInquiry
from app.modules.notification.model import Notification
from app.modules.product_insight.model import ProductInsight
from scripts.check_review_regressions import BASE, call, login

MARKER = f"CHKINS{int(time.time())}"
FAILURES: list[str] = []


def check(label: str, condition: bool, detail: object = "") -> None:
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


def rejected(status: int) -> bool:
    """入参非法：可能回 400（参数校验）或 422（业务规则），都算拒绝。"""
    return status in (400, 422)


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

    admin = login("admin", "admin123")      # 全权
    zhangsan = login("zhangsan", "123456")  # 业务员：只有 product:view
    zhaoliu = login("zhaoliu", "123456")    # 产品评审人：product:manage + review，范围 self
    wangwu = login("wangwu", "123456")      # 财务：无 product:review

    insight_ids: list[int] = []
    inquiry_ids: list[int] = []

    def api(method, path, body=None, expected=200, token=None):
        status, result = call(method, path, token=token or admin, body=body)
        assert status == expected, (method, path, status, result)
        return result.get("data")

    try:
        print("=== 1. 评审人按权限码判：不是「主管」也能评 ===")
        status, result = call("POST", "/product-insights", token=zhangsan,
                              body={"title": f"{MARKER} 业务员想建的"})
        check("没有 product:manage 的人建不了洞察（业务员）",
              status == 403, f"HTTP {status} {result}")

        seed_insight = api("POST", "/product-insights", token=zhaoliu, body={
            "title": f"{MARKER} 防潮纸箱方向",
            "source": "展会",
            "target_customer": "华东食品厂",
            "direction": "做给需要冷链的食品厂",
            "selling_points": "防潮+可回收",
            "price_assumption": 12.5,
            "conclusion": "可以试做",
        })
        insight_ids.append(seed_insight["id"])
        check("产品岗能建洞察", seed_insight["status"] == "draft", seed_insight["status"])
        check("新建的洞察是第 1 轮",
              seed_insight["review_round"] == 1, seed_insight["review_round"])
        check("负责人默认是创建人（否则建完就从自己列表里消失）",
              seed_insight["owner_id"] is not None, seed_insight["owner_id"])

        api("POST", f"/product-insights/{seed_insight['id']}/submit", token=zhaoliu, body={})

        status, result = call("POST", f"/product-insights/{seed_insight['id']}/review",
                              token=zhangsan, body={"approve": True})
        check("没有 product:review 的人不能评审（业务员 403）",
              status == 403, f"HTTP {status} {result}")
        status, result = call("POST", f"/product-insights/{seed_insight['id']}/review",
                              token=wangwu, body={"approve": True})
        check("财务也不能评审", status == 403, f"HTTP {status}")

        # **关键**：换一条由 admin 建的（owner 不是 zhaoliu），让 zhaoliu 来审。
        # 这同时证明两件事：评审人能审**别人**的；而他的数据范围**没被放大**。
        others = api("POST", "/product-insights", token=admin, body={
            "title": f"{MARKER} 别人建的洞察", "direction": "待产品岗评审",
        })
        insight_ids.append(others["id"])
        api("POST", f"/product-insights/{others['id']}/submit", token=admin, body={})
        reviewed = api("POST", f"/product-insights/{others['id']}/review",
                       token=zhaoliu, body={"approve": True, "note": f"{MARKER} 方向可行"})
        check("数据范围=self 的评审人能审别人建的洞察（否则「评审」无从谈起）",
              reviewed["status"] == "approved", reviewed["status"])
        check("评审意见真的存下来了（不是写死的固定文案）",
              reviewed["review_note"] == f"{MARKER} 方向可行", reviewed["review_note"])

        print("=== 2. 内容冻结：待评审不能改、已通过改了要重审 ===")
        frozen_target = api("POST", "/product-insights", token=zhaoliu, body={
            "title": f"{MARKER} 冻结测试", "direction": "原方向",
        })
        insight_ids.append(frozen_target["id"])
        under = api("POST", f"/product-insights/{frozen_target['id']}/submit",
                    token=zhaoliu, body={})
        check("提交后进入待评审", under["status"] == "under_review", under["status"])

        status, result = call("PATCH", f"/product-insights/{frozen_target['id']}",
                              token=zhaoliu, body={"direction": "偷偷改方向"})
        check("待评审期间改关键内容被拒（改了就成了「审的和提交的不是同一份」）",
              status == 422, f"HTTP {status} {result}")

        # 非关键内容不受冻结约束：冻结只针对"评审所针对的内容"
        api("PATCH", f"/product-insights/{frozen_target['id']}", token=zhaoliu,
            body={"images": ["https://example.com/a.png"]})
        check("待评审期间改参考图不受影响（冻结只针对关键内容）", True)

        api("POST", f"/product-insights/{frozen_target['id']}/review",
            token=admin, body={"approve": True, "note": "通过"})
        reopened = api("PATCH", f"/product-insights/{frozen_target['id']}",
                       token=zhaoliu, body={"direction": "改成新方向"})
        check("已通过后改关键内容 → 退回待评审",
              reopened["status"] == "under_review", reopened["status"])
        check("退回时轮次加一（能看出审过几轮）",
              reopened["review_round"] == 2, reopened["review_round"])
        check("退回时清掉了上一轮的评审时间（那一版批准已作废）",
              reopened["reviewed_at"] is None, reopened["reviewed_at"])
        check("上一轮的评审意见保留（新一轮评审有参考）",
              reopened["review_note"] == "通过", reopened["review_note"])

        print("=== 3. 字段校验与 null 清空 ===")
        status, result = call("POST", "/product-insights", token=zhaoliu,
                              body={"title": "   "})
        check("全空白标题被拒（min_length 挡不住三个空格）",
              rejected(status), f"HTTP {status} {result}")

        status, result = call("POST", "/product-insights", token=zhaoliu,
                              body={"title": f"{MARKER} 负价", "price_assumption": -5})
        check("负数价格假设被拒", rejected(status), f"HTTP {status}")

        status, result = call("POST", "/product-insights", token=zhaoliu,
                              body={"title": f"{MARKER} 超长来源", "source": "x" * 100})
        check("超长的市场来源被拒（不再拖到写库才炸）",
              rejected(status), f"HTTP {status}")

        status, result = call("PATCH", f"/product-insights/{frozen_target['id']}",
                              token=zhaoliu, body={"nosuchfield": 1})
        check("多传字段被拒（不静默丢弃，否则前端以为改成了）",
              rejected(status), f"HTTP {status}")

        clearable = api("POST", "/product-insights", token=zhaoliu, body={
            "title": f"{MARKER} 清空测试", "price_assumption": 88,
        })
        insight_ids.append(clearable["id"])
        cleared = api("PATCH", f"/product-insights/{clearable['id']}", token=zhaoliu,
                      body={"price_assumption": None})
        check("传 null 能真正清空价格假设（修复前被静默跳过）",
              cleared["price_assumption"] is None, cleared["price_assumption"])
        untouched = api("PATCH", f"/product-insights/{clearable['id']}", token=zhaoliu,
                        body={"conclusion": "只改结论"})
        check("没传的字段不受影响",
              untouched["price_assumption"] is None, untouched["price_assumption"])
        check("改了的字段确实生效", untouched["conclusion"] == "只改结论", untouched["conclusion"])

        print("=== 4. 转换：统一编号 + 内部开发需求 + 幂等 ===")
        approved = api("POST", "/product-insights", token=zhaoliu, body={
            "title": f"{MARKER} 待转洞察",
            "source": "1688/阿里",
            "target_customer": "还没接触过的客户",
            "direction": "轻量化",
            "price_assumption": 9.9,
        })
        insight_ids.append(approved["id"])
        api("POST", f"/product-insights/{approved['id']}/submit", token=zhaoliu, body={})
        api("POST", f"/product-insights/{approved['id']}/review",
            token=admin, body={"approve": True, "note": "可转"})

        conv_key = f"{MARKER}-CONV"
        converted = api("POST", f"/product-insights/{approved['id']}/convert",
                        token=zhaoliu, body={"request_key": conv_key})
        inquiry_ids.append(converted["inquiry_id"])
        check("转出来的需求有编号（修复前是裸记录，溯源断了）",
              bool(converted.get("inquiry_no")), converted.get("inquiry_no"))
        check("没选客户 → 明确标成「内部开发需求」",
              converted["origin"] == "internal_dev", converted["origin"])
        check("转换返回里带了来源类型标签（前端能直接展示）",
              converted.get("origin_label") == "内部开发需求", converted.get("origin_label"))

        replayed = api("POST", f"/product-insights/{approved['id']}/convert",
                       token=zhaoliu, body={"request_key": conv_key})
        check("重复转换返回既有需求（不会建第二条）",
              replayed["inquiry_id"] == converted["inquiry_id"],
              f"{replayed['inquiry_id']} vs {converted['inquiry_id']}")

        async with SessionLocal() as session:
            same_insight = (
                await session.execute(
                    select(CustomInquiry).where(
                        CustomInquiry.source_insight_id == approved["id"],
                        CustomInquiry.deleted_at.is_(None),
                    )
                )
            ).scalars().all()
        check("库里确实只有一条来源需求", len(same_insight) == 1, len(same_insight))
        check("需求上记下了来源洞察（能回链）",
              same_insight[0].origin == "internal_dev", same_insight[0].origin)

        print("=== 5. 权限连续性：内部开发需求不因「没客户」而人人可见 ===")
        # 用详情接口而不是列表 keyword：询价的 keyword 只搜标题，
        # 拿编号去搜本来就搜不到（搜索行为没问题，是断言选错了字段）。
        detail_inq = api("GET", f"/custom-inquiries/{converted['inquiry_id']}", token=zhaoliu)
        check("提出者自己看得到", detail_inq["id"] == converted["inquiry_id"],
              detail_inq["id"])
        check("接口返回里带来源类型（列表能一眼分辨是不是客户需求）",
              detail_inq.get("origin") == "internal_dev", detail_inq.get("origin"))

        # 用 zhangsan（业务员，**数据范围=本人**）来测；wangwu 是财务、范围=all，
        # 他本来就看得到全部数据，拿他测等于没测（第一版就栽在这里）。
        status, peek = call("GET", f"/custom-inquiries/{converted['inquiry_id']}", token=zhangsan)
        check("另一个「仅本人」范围的账号看不到它（修复前无客户记录一律放行）",
              status == 403, f"HTTP {status} {peek}")

        # 反证：**正常**的"客户还没定"询价仍然对同事可见——别把口子一刀切掉。
        # 直接拿 id 查详情：中文标题拼进 query 要 URL 编码，拼错会变成 400，
        # 那时看到的"拒绝"就不是权限在起作用了。
        plain = api("POST", "/custom-inquiries", body={
            "title": f"{MARKER} 客户未定的普通询价",
        })
        inquiry_ids.append(plain["id"])
        status, _ = call("GET", f"/custom-inquiries/{plain['id']}", token=zhangsan)
        check("普通的「客户还没定」询价仍按原口径可见（没有一刀切）",
              status == 200, f"HTTP {status}")

        print("=== 5.1 内部开发需求：评审岗之间互相可见（口径 2026-10-06）===")
        # 造一条由**管理员**转出的内部开发需求（提出者不是 zhaoliu）：
        # zhaoliu 的范围是 self，按旧口径他看不到，按新口径（持 product:review）应当看得到。
        rev_insight = api("POST", "/product-insights", body={
            "title": f"{MARKER} 管理员提的洞察", "direction": "评审岗共享测试",
        })
        insight_ids.append(rev_insight["id"])
        api("POST", f"/product-insights/{rev_insight['id']}/submit", body={})
        api("POST", f"/product-insights/{rev_insight['id']}/review",
            token=zhaoliu, body={"approve": True, "note": "可转"})
        rev_conv = api("POST", f"/product-insights/{rev_insight['id']}/convert",
                       token=admin, body={"request_key": f"{MARKER}-REV"})
        inquiry_ids.append(rev_conv["inquiry_id"])

        status, peek_rev = call("GET", f"/custom-inquiries/{rev_conv['inquiry_id']}", token=zhaoliu)
        check("评审岗看得到别人提的内部开发需求（换岗/换人后不断线）",
              status == 200, f"HTTP {status} {peek_rev}")
        status, peek_plain_role = call(
            "GET", f"/custom-inquiries/{rev_conv['inquiry_id']}", token=zhangsan
        )
        check("但**没有** product:review 的人仍然看不到（放开只针对评审岗）",
              status == 403, f"HTTP {status}")

        print("=== 5.2 修订后来源不能丢（审视第 8 条：改一次就对全体开放）===")
        # 审视原话：「询价修订接口没有复制 origin / source_insight_id / extra。
        # 新版本的 origin 默认是 customer，所以无客户的内部开发需求修订后，
        # 会变成按普通无客户询价规则开放的记录」——这是**权限泄露**，不是显示问题：
        # 一条内部开发需求只要被修订一次，就对全体同事可见了。
        rev2 = api("POST", f"/custom-inquiries/{converted['inquiry_id']}/revise",
                   token=admin, body={"revision_note": f"{MARKER} 修订以验来源",
                                      "title": f"{MARKER} 修订版标题"})
        inquiry_ids.append(rev2["id"])
        check("修订后新版本仍是「内部开发需求」（origin 跟着走）",
              rev2.get("origin") == "internal_dev", rev2.get("origin"))
        check("修订后结构化来源还在（extra 跟着走，回链不断）",
              bool((rev2.get("extra") or {}).get("insight_id")),
              rev2.get("extra"))
        # 关键：修完之后，没有评审权的同事**仍然看不到**
        status, peek2 = call("GET", f"/custom-inquiries/{rev2['id']}", token=zhangsan)
        check("修订后「仅本人」范围的同事仍看不到（修复前这里会 200）",
              status == 403, f"HTTP {status} {peek2}")
        # 历史接口也要逐版本判权限：只校验入口版本的话，拿可见的 V2 进来
        # 就能把整条链（含 V1 的内部资料）一起读出来。
        status, hist = call("GET", f"/custom-inquiries/{rev2['id']}/history", token=zhangsan)
        check("「仅本人」同事连历史链都拿不到", status == 403, f"HTTP {status}")
        hist_ids = [r["id"] for r in api("GET", f"/custom-inquiries/{rev2['id']}/history",
                                        token=admin)]
        check("管理员仍能看到整条链（V1 + V2 都在）",
              converted["inquiry_id"] in hist_ids and rev2["id"] in hist_ids, hist_ids)

        print("=== 6. 删除保护与来源回链 ===")
        status, result = call("DELETE", f"/product-insights/{approved['id']}", token=zhaoliu)
        check("已转需求的洞察不能删（删了来源就断了）",
              status == 422, f"HTTP {status} {result}")

        detail = api("GET", f"/product-insights/{approved['id']}", token=zhaoliu)
        check("洞察详情仍能指出转到了哪条需求",
              detail["converted_inquiry_id"] == converted["inquiry_id"],
              detail["converted_inquiry_id"])
        check("洞察详情返回参考图字段（此前四个环节都没打通）",
              "images" in detail, sorted(detail))
        with_image = api("GET", f"/product-insights/{frozen_target['id']}", token=zhaoliu)
        check("参考图真的存下来并读得回来",
              with_image["images"] == ["https://example.com/a.png"], with_image["images"])

        status, result = call("POST", f"/product-insights/{seed_insight['id']}/review",
                              token=admin, body={"approve": False})
        check("否决时没写意见被拒（后端拦住，不能只靠前端）",
              rejected(status), f"HTTP {status} {result}")

    finally:
        async with SessionLocal() as session:
            if inquiry_ids:
                await session.execute(
                    delete(CustomInquiry).where(CustomInquiry.id.in_(inquiry_ids))
                )
            if insight_ids:
                await session.execute(
                    delete(ProductInsight).where(ProductInsight.id.in_(insight_ids))
                )
            await session.execute(
                delete(AuditLog).where(AuditLog.after_data.cast(String).contains(MARKER))
            )
            await session.execute(
                delete(Notification).where(Notification.content.contains(MARKER))
            )
            # **必须显式提交**：`async with SessionLocal()` 退出时只 close，
            # 没提交的事务整体回滚，上面那些 delete 就全白写了。
            await session.commit()

    if FAILURES:
        print(f"\n失败 {len(FAILURES)} 项：{FAILURES}")
        raise SystemExit(1)
    print(
        "\nOK 洞察评审冻结与轮次、字段校验与清空、转换编号与幂等、"
        "内部开发需求权限连续性、删除保护、评审权限码"
    )


if __name__ == "__main__":
    asyncio.run(main())
