"""企业微信 Adapter（05-TECH §14 的 WeCom Adapter）。

分层意图：**业务代码不碰企微的 HTTP 细节**。service 只调这里的方法，
拿到的永远是"已经转成 CRM 口径"的字典；换 HTTP 库、改接口版本只动这个文件。

关于"还没拿到凭据"这件事的处理原则：
- 不 mock、不返回假数据。缺凭据就抛 `WeComNotConfigured`，
  让接口层翻译成明确的业务提示（"请先在企业微信配置里填 corp id 和 secret"）。
  静默返回空列表最危险——同步任务会显示"成功 0 条"，没人知道其实是没配。
- 所有真实 HTTP 调用都按企微文档的接口写好了，凭据一填就能直接用。

接口对应关系（企微官方文档）：
  gettoken                    取 access_token
  department/list             部门列表
  user/list                   部门成员
  user/get                    成员详情
  externalcontact/list        客户联系：外部联系人列表（需客户联系 secret）
  externalcontact/get         外部联系人详情
  externalcontact/remark      设置备注（用于回写）
  externalcontact/transfer    离职继承：分配在职成员的客户
  externalcontact/groupchat/transfer  离职继承：分配群聊
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import settings


class WeComError(RuntimeError):
    """企微返回了业务错误（errcode != 0）。"""

    def __init__(self, errcode: int, errmsg: str, api: str) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        self.api = api
        super().__init__(f"企业微信接口 {api} 返回错误 {errcode}：{errmsg}")


class WeComNotConfigured(RuntimeError):
    """还没配凭据。接口层据此返回 50202 / 明确的业务提示。"""

    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(f"企业微信还没配置：缺少 {missing}，请先在后端 .env 里补齐")


# 企微 access_token 有效期 7200 秒，提前 5 分钟过期，避免边界上用到失效 token
_TOKEN_TTL = 7200 - 300
_token_cache: dict[str, tuple[str, float]] = {}


class WeComClient:
    """企微 API 客户端。凭据从全局配置读，不把密钥散落在调用点。"""

    def __init__(self, *, http: httpx.AsyncClient | None = None) -> None:
        self._http = http
        self._owns_http = http is None

    # ---- 基础设施 ---------------------------------------------------------

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=settings.wecom_api_base, timeout=20.0)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and self._owns_http:
            await self._http.aclose()
            self._http = None

    async def _call(self, api: str, *, params: dict[str, Any] | None = None,
                    json_body: dict[str, Any] | None = None,
                    access_token: str | None = None) -> dict[str, Any]:
        query = dict(params or {})
        if access_token:
            query["access_token"] = access_token
        client = await self._client()
        response = await client.request(
            "POST" if json_body is not None else "GET",
            f"/cgi-bin/{api}",
            params=query,
            json=json_body,
        )
        response.raise_for_status()
        payload = response.json()
        errcode = int(payload.get("errcode", 0))
        if errcode != 0:
            raise WeComError(errcode, payload.get("errmsg", ""), api)
        return payload

    # ---- 凭据 -------------------------------------------------------------

    def contact_ready(self) -> bool:
        return settings.wecom_contact_ready

    def external_ready(self) -> bool:
        return settings.wecom_external_ready

    async def access_token(self, *, external: bool = False) -> str:
        """取 access_token 并进程内缓存。

        `external=True` 用客户联系 secret（外部联系人接口必须用这一把，
        用通讯录 secret 调会返回 60011 无权限）。
        """
        if not settings.wecom_corp_id:
            raise WeComNotConfigured("WECOM_CORP_ID")
        secret = (
            settings.wecom_external_contact_secret if external else settings.wecom_contact_secret
        )
        field = (
            "WECOM_EXTERNAL_CONTACT_SECRET" if external else "WECOM_CONTACT_SECRET"
        )
        if not secret:
            raise WeComNotConfigured(field)

        cache_key = f"{settings.wecom_corp_id}:{'ext' if external else 'contact'}"
        cached = _token_cache.get(cache_key)
        if cached and cached[1] > time.time():
            return cached[0]

        payload = await self._call(
            "gettoken",
            params={"corpid": settings.wecom_corp_id, "corpsecret": secret},
        )
        token = str(payload["access_token"])
        _token_cache[cache_key] = (token, time.time() + _TOKEN_TTL)
        return token

    # ---- 通讯录（PRD §8.1）------------------------------------------------

    async def list_departments(self) -> list[dict[str, Any]]:
        """拉全部部门。企微这个接口一次性返回整棵部门树，不分页。"""
        token = await self.access_token()
        payload = await self._call("department/list", access_token=token)
        return list(payload.get("department", []))

    async def list_department_users(
        self, department_id: int, *, fetch_child: bool = False
    ) -> list[dict[str, Any]]:
        """拉一个部门下的成员（detailed 模式带上姓名/手机/邮箱）。"""
        token = await self.access_token()
        payload = await self._call(
            "user/list",
            params={
                "department_id": department_id,
                "fetch_child": 1 if fetch_child else 0,
            },
            access_token=token,
        )
        return list(payload.get("userlist", []))

    # ---- 外部联系人（PRD §8.2）-------------------------------------------

    async def list_external_contacts(
        self, *, wecom_userid: str | None = None, cursor: str = ""
    ) -> dict[str, Any]:
        """拉外部联系人。

        两种口径：
        - 不传 wecom_userid：企业全部客户，`GET externalcontact/list`
        - 传 wecom_userid：某个成员名下的客户，`GET externalcontact/list/{userid}`
        """
        token = await self.access_token(external=True)
        api = (
            f"externalcontact/list/{wecom_userid}"
            if wecom_userid
            else "externalcontact/list"
        )
        return await self._call(
            api,
            params={"cursor": cursor} if cursor else None,
            access_token=token,
        )

    async def get_external_contact(self, external_userid: str) -> dict[str, Any]:
        token = await self.access_token(external=True)
        payload = await self._call(
            "externalcontact/get",
            params={"external_userid": external_userid},
            access_token=token,
        )
        return payload.get("external_contact", {}) or {}

    async def get_follow_users(self, external_userid: str) -> list[dict[str, Any]]:
        """谁加了这个人（跟进关系）。"""
        token = await self.access_token(external=True)
        payload = await self._call(
            "externalcontact/get_follow_user_list",
            params={"external_userid": external_userid},
            access_token=token,
        )
        return list(payload.get("follow_user", []))

    async def list_all_external_contacts(self) -> list[dict[str, Any]]:
        """翻页拉全量外部联系人，带上每条的详情（详情接口才给 name/avatar）。"""
        results: list[dict[str, Any]] = []
        cursor = ""
        for _ in range(max(1, settings.wecom_sync_max_pages)):
            payload = await self.list_external_contacts(cursor=cursor)
            for row in payload.get("external_contact_list", []) or []:
                contact = row.get("external_contact", {}) or {}
                external_userid = contact.get("external_userid")
                if not external_userid:
                    continue
                # list 接口只给 external_userid 和少量字段，详情要单独拉
                detail = await self.get_external_contact(str(external_userid))
                merged = {**contact, **detail}
                merged.setdefault("follow_user", row.get("follow_info") or {})
                results.append(merged)
            cursor = str(payload.get("next_cursor") or "")
            if not cursor:
                break
        return results

    # ---- 离职继承（PRD §8.4）---------------------------------------------

    async def transfer_customer(
        self, *, external_userid: str, handover_userid: str, takeover_userid: str
    ) -> dict[str, Any]:
        """把某个客户的跟进关系从离职成员转给接手成员。

        注意：企微这条接口只交接**客户关系**；CRM 侧的负责人、商机、
        任务由 service 自己按业务规则转移（PRD §8.4 要求保留创建人、
        历史跟进、历史报价与日志，所以不能整行改负责人了事）。
        """
        token = await self.access_token(external=True)
        return await self._call(
            "externalcontact/transfer",
            json_body={
                "external_userid": external_userid,
                "handover_userid": handover_userid,
                "takeover_userid": takeover_userid,
            },
            access_token=token,
        )

    # ---- 事件回调（API §10 POST /webhooks/wecom/events）-------------------

    def callback_ready(self) -> bool:
        return bool(
            settings.wecom_callback_token
            and settings.wecom_callback_aes_key
            and settings.wecom_corp_id
        )

    async def send_text_card(
        self, *, to_user: str, title: str, description: str, url: str | None = None
    ) -> dict[str, Any]:
        """发应用消息（通知的企微渠道，PRD §25）。

        有跳转链接用 textcard（卡片可点击跳转）；没有链接时改发 text——
        textcard 的 url 是必填项，空串会被企微以 41010 拒绝。
        """
        if not settings.wecom_agent_id:
            raise WeComNotConfigured("WECOM_AGENT_ID")
        token = await self.access_token()
        if url:
            body: dict[str, Any] = {
                "touser": to_user,
                "msgtype": "textcard",
                "agentid": int(settings.wecom_agent_id),
                "textcard": {
                    "title": title,
                    "description": description,
                    "url": url,
                },
            }
        else:
            body = {
                "touser": to_user,
                "msgtype": "text",
                "agentid": int(settings.wecom_agent_id),
                "text": {"content": f"{title}\n{description}"},
            }
        return await self._call("message/send", json_body=body, access_token=token)


_client: WeComClient | None = None


def get_client() -> WeComClient:
    """进程内单例：access_token 缓存靠它复用，别每次请求都新建。"""
    global _client
    if _client is None:
        _client = WeComClient()
    return _client
