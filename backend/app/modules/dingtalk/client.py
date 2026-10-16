"""钉钉开放平台客户端。

只做三件事：取 token（带缓存）、发起审批实例、查询实例状态。

**关于"未配置"**：与 `ErpNotConfigured` / `WeComNotConfigured` 同一套做法——
缺配置直接抛，接口层翻成明确说明。绝不返回"成功"然后什么都没做，
那种假成功在联调时最费时间（看着通了，实际一条单没建）。

**最容易踩的坑**：发起审批实例请求体里，表单值挂在 **`formComponentValues`**
这个数组上（元素形如 `{"name": ..., "id": "TextField_XXX", "value": ...}`），
**没有 `formValues` 这个字段**——这名字是错的（我们原来就写错了，
后来拿官方 SDK `alibabacloud_dingtalk` 的 `StartProcessInstanceRequest`
核对才发现：模型里只有 `form_component_values`，而且它是必填）。
另一层坑是**控件对不上时钉钉不报错，只把那格留空**：于是"预填成功"的假象下
业务还得手填一遍，正好把"免重复录入"这条验收标准踩没。所以模板的字段清单
（控件 id + 类型）是联调前必须拿到的输入。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import settings

#: access_token 有效期 7200 秒；提前 5 分钟判过期，避免卡在过期那一刻的请求失败
_TOKEN_SKEW_SECONDS = 300

DINGTALK_DISABLED_MESSAGE = "钉钉外部调用已关闭（DINGTALK_PUSH_OFF），开发阶段未执行外部请求"

#: 扫码登录自己的闸。**与审批总闸是两件事**（2026-10-14 拆开）：
#: 登录是用户自己要把自己放进来，总闸管的是"系统主动去打扰钉钉/同事"。
DINGTALK_LOGIN_DISABLED_MESSAGE = (
    "钉钉扫码登录已关闭（DINGTALK_LOGIN_OFF），开发阶段未开放"
)


class DingTalkDisabled(RuntimeError):
    """某个外部调用闸门关着：在获取 token 或创建 HTTP 客户端前拦截。

    默认说的是审批总闸那句话；**扫码登录**有自己的闸，抛它自己那句
    （见 `_require_login`）—— 两件事分属不同开关，报错也该各说各的。
    """

    def __init__(self, message: str = DINGTALK_DISABLED_MESSAGE) -> None:
        super().__init__(message)


class DingTalkError(RuntimeError):
    """钉钉接口返回了业务错误（对方明确答复了，所以**确定没有建单**）。

    `http_status` 必须尽量带上：5xx 说明对方可能已经处理了请求、只是回包失败，
    与 4xx 的"明确拒绝"在业务后果上完全不同（见 `classify_outcome`）。
    """

    def __init__(
        self,
        message: str,
        *,
        api: str,
        code: str | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
    ) -> None:
        self.api = api
        self.code = code
        self.http_status = http_status
        self.request_id = request_id
        suffix = f'（requestId: {request_id}）' if request_id else ''
        super().__init__(f"钉钉接口 {api} 返回错误：{message}{suffix}")


class DingTalkUnknownOutcome(RuntimeError):
    """请求**可能已经到达钉钉**，但我们读不出结果（超时/连接断/响应不可解析）。

    这是最需要小心的一类：发起审批接口没有幂等键，所以"结果未知"时自动重发
    会在对方系统里建出第二张单。归到这类之后只能先查询或人工核对。
    """


#: 一次"发起审批"的三种后果（第七批 7.7 要求分开）。对"能不能重试"的答案完全不同。
OUTCOME_NOT_SENT = "not_sent"
OUTCOME_REJECTED = "rejected"
OUTCOME_UNKNOWN = "unknown"


def classify_outcome(exc: BaseException) -> str:
    """把发起审批时的异常翻译成三种后果。**判断只写在这一处**。

    为什么不能 `except Exception` 一刀切（早先就是这么写的，于是超时被记成
    "发起失败"，而"发起失败"又能被同轮重试——重复建单的入口就开在那里）：

    - `OUTCOME_NOT_SENT`：确定没发出去。缺配置、推送闸门、取 token 失败，
      以及参数拼装这类本地错误——请求根本没出去，同轮重试安全；
    - `OUTCOME_REJECTED`：外部明确拒绝（4xx、业务错误码，或 200 但没给 instanceId）。
      单子没建出来，同轮重试同样安全；
    - `OUTCOME_UNKNOWN`：超时、连接中断、响应读不出来、5xx。**对方可能已经建单**，
      只能先查询/人工核对，绝不能再发一次。

    `DingTalkError.http_status` 缺失时按 UNKNOWN 保守处理：宁可多让人看一眼，
    也不要因为少带一个状态码就自动重发。
    """
    if isinstance(exc, (DingTalkDisabled, DingTalkNotConfigured)):
        return OUTCOME_NOT_SENT
    if isinstance(exc, DingTalkUnknownOutcome):
        return OUTCOME_UNKNOWN
    if isinstance(exc, httpx.HTTPError):
        # httpx 的错误都发生在"请求已经出去"之后（TimeoutException / TransportError…）
        return OUTCOME_UNKNOWN
    if isinstance(exc, DingTalkError):
        if exc.http_status is None or exc.http_status >= 500:
            return OUTCOME_UNKNOWN
        return OUTCOME_REJECTED
    # 其余异常发生在 HTTP 请求之前（参数拼装等本地错误）→ 确定没发出去
    return OUTCOME_NOT_SENT


class DingTalkNotConfigured(RuntimeError):
    """缺配置。带上是缺哪一项——报错要能直接指向要补的东西。"""

    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(
            f"钉钉未配置（缺 {missing}）。请在 backend/.env 补上，并确认"
            "开放平台的权限已勾选、版本已发布（权限不发布不生效）"
        )


class DingTalkClient:
    def __init__(self) -> None:
        self._token: str | None = None
        self._token_expires_at: float = 0.0
        self._schema_cache: dict = {}

    async def get_display_process_schema(self, process_code: str) -> dict:
        """只读页面缓存模板，避免页面每次刷新都消耗钉钉配额；提交仍重新读。"""
        self._require()
        from copy import deepcopy
        cached = self._schema_cache.get(process_code)
        if cached and cached[0] > time.monotonic():
            return deepcopy(cached[1])
        schema = await self.get_process_schema(process_code)
        self._schema_cache[process_code] = (time.monotonic() + 300, deepcopy(schema))
        return schema

    @property
    def configured(self) -> bool:
        return bool(settings.dingtalk_app_key and settings.dingtalk_app_secret)

    def _require(self) -> None:
        if settings.dingtalk_push_off:
            raise DingTalkDisabled()
        if not settings.dingtalk_app_key:
            raise DingTalkNotConfigured("DINGTALK_APP_KEY")
        if not settings.dingtalk_app_secret:
            raise DingTalkNotConfigured("DINGTALK_APP_SECRET")

    def _require_login(self) -> None:
        """扫码登录的就绪判据：**只看登录自己那把闸**，不看审批总闸。

        两把闸为什么必须是两把（2026-10-14 拆开）：
        `dingtalk_push_off` 的用意是"开发期别让**系统**主动去打扰钉钉/同事"
        （审批发起、通讯录同步、通知推送）。而扫码登录是**用户自己**要把自己
        放进来 —— 让用户登录这件事，和让系统替他建单/发通知，是两种动作。
        共用一把闸的后果就是"想开登录，就得连审批一起打开"。

        凭据的两项检查与 `_require` 一致：闸开着但没配 AppKey，照样得明确报出来。
        """
        if settings.dingtalk_login_off:
            raise DingTalkDisabled(DINGTALK_LOGIN_DISABLED_MESSAGE)
        if not settings.dingtalk_app_key:
            raise DingTalkNotConfigured("DINGTALK_APP_KEY")
        if not settings.dingtalk_app_secret:
            raise DingTalkNotConfigured("DINGTALK_APP_SECRET")

    async def access_token(self, *, for_login: bool = False) -> str:
        """企业内部应用 access_token，带进程内缓存。

        `for_login=True`：走**登录那条链路**（扫码登录要把 unionId 换成 userid，
        需要企业内部令牌），此时查**登录自己那把闸**，而不是审批总闸。

        闸的检查放在缓存判断**之前**：闸一关，缓存里的旧令牌也照样不给 ——
        否则"关了闸还会因为缓存而发出请求"，与关闸的用意正好相反。
        """
        if for_login:
            self._require_login()
        else:
            self._require()
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        try:
            async with httpx.AsyncClient(timeout=10) as http:
                resp = await http.post(
                    f"{settings.dingtalk_base_url}/v1.0/oauth2/accessToken",
                    json={
                        "appKey": settings.dingtalk_app_key,
                        "appSecret": settings.dingtalk_app_secret,
                    },
                )
        except httpx.HTTPError as exc:
            raise DingTalkError(
                f"获取 access_token 失败：{exc}", api="oauth2/accessToken"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise DingTalkError(
                "获取 access_token 的响应不是合法 JSON",
                api="oauth2/accessToken",
                http_status=resp.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise DingTalkError(
                f"access_token 响应结构错误：{type(data).__name__}",
                api="oauth2/accessToken",
                http_status=resp.status_code,
            )
        if resp.status_code != 200 or not data.get("accessToken"):
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="oauth2/accessToken",
                http_status=resp.status_code,
            )
        self._token = data["accessToken"]
        self._token_expires_at = (
            now + int(data.get("expireIn", 7200)) - _TOKEN_SKEW_SECONDS
        )
        return self._token

    async def create_process_instance(
        self,
        *,
        process_code: str,
        form_component_values: list[dict[str, Any]],
        originator_user_id: str,
        dept_id: int | None = None,
    ) -> str:
        """发起审批实例，返回 instance_id（场景11：从 CRM 发起询价审批）。

        三个必填项（官方 SDK 里标了 "This parameter is required."）：
        `processCode` / `originatorUserId`（**钉钉 userid**）/ `formComponentValues`。
        """
        self._require()
        if not process_code:
            raise DingTalkNotConfigured("审批模板 process_code")
        try:
            token = await self.access_token()
        except (DingTalkError, httpx.HTTPError) as exc:
            # 取 token 这一步失败，审批单**一定还没建**：归"确定没发出"（同轮重试安全）。
            # 不能因为它是 5xx/超时就报"结果未知"，那会白白惊动人工核对。
            raise DingTalkError(
                f"获取 access_token 失败：{exc}", api="oauth2/accessToken", http_status=400
            ) from exc
        body: dict[str, Any] = {
            "processCode": process_code,
            "originatorUserId": originator_user_id,
            # 键名就是 `formComponentValues`：官方 SDK 的
            # `StartProcessInstanceRequest.form_component_values` 序列化出来正好是这个。
            # 曾经写成 `formValues`——那不是钉钉的字段，钉钉会当没传表单值。
            "formComponentValues": form_component_values,
        }
        if dept_id is not None:
            body["deptId"] = dept_id
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    f"{settings.dingtalk_base_url}/v1.0/workflow/processInstances",
                    json=body,
                    headers={"x-acs-dingtalk-access-token": token},
                )
        except httpx.HTTPError as exc:
            # **请求已经发出去了**，只是没等到响应（超时/连接断）。
            # 钉钉那边可能已经建单，所以必须报"结果未知"而不是"失败"。
            raise DingTalkUnknownOutcome(
                f"发起审批实例时外部请求没有得到响应（{type(exc).__name__}）：{exc}"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            # 响应体读不出来：HTTP 已经跑完一轮，同样属于"结果未知"
            raise DingTalkUnknownOutcome(
                "发起审批实例后响应无法解析，无法确认是否已建单"
            ) from exc
        if resp.status_code != 200 or not data.get("instanceId"):
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="workflow/processInstances",
                code=data.get('code'),
                http_status=resp.status_code,
                request_id=data.get('requestid') or data.get('requestId') or resp.headers.get('x-acs-request-id'),
            )
        return data["instanceId"]

    async def get_process_instance(self, instance_id: str) -> dict[str, Any]:
        """查审批实例状态与结果（回写用；没有回调时靠轮询它）。"""
        self._require()
        token = await self.access_token()
        # ⚠️ 读路径也要把 httpx 的异常**包成我们自己的异常**（2026-10-14 审查修），
        # 与上面写路径（`create_process_instance`）同一手法。
        #
        # 原来这里裸着：`httpx.TimeoutException / ConnectError`（不是 `DingTalkError`）
        # 与 `resp.json()` 的 `JSONDecodeError` 会直接穿透调用方
        # `sync_pending_instances` 的 `except DingTalkError` ——
        # 后果是**一条超时打断整批**（后面几十条不再轮询），而且本轮的改动
        # 只在循环末尾才 flush/commit，异常路径事务回滚 →
        # **前面已取回的状态全部丢失**；手工触发的 `/dingtalk/oa-sync` 还会 500。
        # 这与该函数自己的承诺（"单条查失败不影响其他单，记在行上，下一轮还会再试"）
        # 直接矛盾，也与 `classify_outcome` 的口径不一致（它把 `httpx.HTTPError`
        # 明确归为"结果未知"）。
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(
                    f"{settings.dingtalk_base_url}/v1.0/workflow/processInstances",
                    params={"processInstanceId": instance_id},
                    headers={"x-acs-dingtalk-access-token": token},
                )
        except httpx.HTTPError as exc:
            # 请求已经发出去了，只是没等到响应 —— 状态未知，不能当成"失败"
            raise DingTalkUnknownOutcome(
                f"查询审批实例时外部请求没有得到响应（{type(exc).__name__}）：{exc}"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            # 响应体读不出来（网关错误页 / WAF 拦截页）：同样属于"结果未知"
            raise DingTalkUnknownOutcome(
                "查询审批实例后响应无法解析，无法确认审批结果"
            ) from exc
        if resp.status_code != 200:
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="workflow/processInstances:get",
                http_status=resp.status_code,
            )
        # 钉钉把审批内容是**包在 `result` 里**的，顶层只有 requestId 之类。
        # 早先按顶层 `instanceId` 判断成功，于是正常响应被误报成"接口失败"
        # ——结果是"结果回收"这条链悄悄不工作（审批批完了 CRM 还显示审批中）。
        # 判据改成"里面有没有审批内容"，拿不到就算失败。
        inner = data.get("result") or data
        if not inner.get("title") and not inner.get("status"):
            raise DingTalkError("响应里没有审批内容", api="workflow/processInstances:get")
        # DWS/历史实例使用的别名与新版读取结果归一，原字段仍保留。
        inner = dict(inner)
        if not inner.get("result"):
            inner["result"] = inner.get("processInstanceResult")
        if not inner.get("formComponentValues"):
            inner["formComponentValues"] = inner.get("formValueVOS") or []
        if not inner.get("originatorUserId"):
            inner["originatorUserId"] = inner.get("originatorUserid")
        return inner

    async def terminate_process_instance(
        self, *, instance_id: str, operating_user_id: str, remark: str
    ) -> dict[str, Any]:
        """撤回当前发起人尚未完成的审批实例。"""
        self._require()
        token = await self.access_token()
        body = {
            "processInstanceId": instance_id,
            "isSystem": False,
            "remark": remark,
            "operatingUserId": operating_user_id,
        }
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    f"{settings.dingtalk_base_url}/v1.0/workflow/processInstances/terminate",
                    json=body,
                    headers={"x-acs-dingtalk-access-token": token},
                )
        except httpx.HTTPError as exc:
            raise DingTalkUnknownOutcome(f"撤回审批时外部请求没有得到响应：{exc}") from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise DingTalkUnknownOutcome("撤回审批后响应无法解析，无法确认撤回结果") from exc
        if not isinstance(data, dict) or resp.status_code >= 500:
            raise DingTalkUnknownOutcome("钉钉撤回响应异常，请等待同步或核对原单")
        if resp.status_code != 200 or data.get("success") is False or data.get("result") is False:
            raise DingTalkError(
                data.get("message") or data.get("errmsg") or resp.text[:200],
                api="workflow/processInstances/terminate",
                http_status=resp.status_code,
            )
        if data.get('success') is not True and data.get('result') is not True:
            raise DingTalkUnknownOutcome('钉钉未明确确认撤回结果，请等待同步或核对原单')
        return data

    async def get_process_schema(self, process_code: str) -> dict[str, Any]:
        """拉某个模板的完整表单结构（含每个控件的 componentId）。

        预填必须用 **componentId**，不是中文名称——名字对不上钉钉不报错、
        只把那格留空，于是"预填成功"的假象下业务还得手填一遍。
        所以字段映射一律以这个方法的结果为准，不靠人抄。

        接口形状是**按模板查**：`GET .../forms/schemas/processCodes?processCode=xxx`。
        不带 processCode 会被拒（"processCode is mandatory"），
        POST 到同一路径是 404——两个都实测过，别再按"拉全部"的思路写。
        "公司里有哪些审批模板"要用管理端接口或让管理员从后台看。
        """
        self._require()
        token = await self.access_token()
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.get(
                f"{settings.dingtalk_base_url}/v1.0/workflow/forms/schemas/processCodes",
                params={"processCode": process_code},
                headers={"x-acs-dingtalk-access-token": token},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="forms/schemas/processCodes",
                http_status=resp.status_code,
            )
        return data.get("result") or data

    async def upload_media(
        self, *, content: bytes, filename: str, media_type: str = "image"
    ) -> str:
        """上传图片/附件，返回 media_id。

        审批模板里的「产品参考图片」是**必填**的图片控件，而图片不能直接塞进
        审批单——必须先用这个接口换成 media_id 再填。所以"从 CRM 带图过去"
        这一步绕不开。

        用的是旧版 `oapi.dingtalk.com/media/upload`（它按 multipart 收文件、
        返回 media_id），新版接口没有等价能力。
        """
        self._require()
        token = await self.access_token()
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post(
                "https://oapi.dingtalk.com/media/upload",
                params={"access_token": token, "type": media_type},
                files={"media": (filename, content)},
            )
        data = resp.json() if resp.content else {}
        if data.get("errcode") or not data.get("media_id"):
            raise DingTalkError(
                data.get("errmsg") or resp.text[:200],
                api="media/upload",
                http_status=resp.status_code,
            )
        return str(data["media_id"])


    async def search_user_id_by_name(self, name: str) -> str | None:
        """按姓名搜人，返回 userid（钉钉认编号不认姓名）。

        审批模板里的「提报人」「对接报价员」都是联系人控件，填的是**人的编号**。
        通讯录搜索接口返回的就是编号列表，所以 CRM 的用户名要能对上钉钉的人名
        ——对不上就明确返回 None，由调用方报"这个人钉钉里没有"，而不是瞎填一个。

        注意参数：这个接口**必须带 `offset`**，否则报 "offset is mandatory"，
        很容易被误判成权限问题（我们就误判过一次）。
        """
        self._require()
        token = await self.access_token()
        try:
            async with httpx.AsyncClient(timeout=20) as http:
                resp = await http.post(
                    f"{settings.dingtalk_base_url}/v1.0/contact/users/search",
                    headers={"x-acs-dingtalk-access-token": token},
                    json={"queryWord": name, "offset": 0, "size": 20},
                )
        except httpx.HTTPError as exc:
            raise DingTalkError(
                f"查询钉钉成员失败：{exc}", api="contact/users/search"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise DingTalkError(
                "钉钉成员搜索响应不是合法 JSON",
                api="contact/users/search",
                http_status=resp.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise DingTalkError(
                f"钉钉成员搜索响应结构错误：{type(data).__name__}",
                api="contact/users/search",
                http_status=resp.status_code,
            )
        if resp.status_code != 200:
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="contact/users/search",
                http_status=resp.status_code,
            )
        # 该接口返回的是用户对象列表（通常在 `list`，部分网关会包在
        # `result.list`），不是 userid 字符串列表。原来直接 `str(user)` 会把
        # 整个字典写进审批表单，例如 `{'userid': 'u1', 'name': '张三'}`，
        # 钉钉随后无法识别这个发起人。
        raw_result = data.get("result")
        if isinstance(raw_result, list):
            users = raw_result
        else:
            result = raw_result if isinstance(raw_result, dict) else data
            users = result.get("list") or []
        if not isinstance(users, list):
            return None

        requested = name.strip()
        candidates: list[tuple[str, str | None]] = []
        for user in users:
            if isinstance(user, str):
                userid = user.strip()
                user_name = None
            elif isinstance(user, dict):
                userid = str(user.get("userid") or user.get("userId") or "").strip()
                user_name = str(user.get("name") or "").strip() or None
            else:
                continue
            if userid:
                candidates.append((userid, user_name))

        # 精确匹配优先：搜“子木”可能带回一串包含这两个字的人。
        for userid, user_name in candidates:
            if user_name == requested:
                return userid
        # 响应只有 userid、没有姓名时，多个候选不能擅自挑第一个；调用方应让
        # 管理员补充绑定，否则可能把审批单记到同名同事名下。
        return candidates[0][0] if len(candidates) == 1 else None

    async def get_user_dept_ids(self, user_id: str) -> list[int]:
        """取某人的部门编号列表（「提报部门」控件要的是部门编号，不是部门名）。"""
        self._require()
        token = await self.access_token()
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                "https://oapi.dingtalk.com/topapi/v2/user/get",
                params={"access_token": token},
                json={"userid": user_id},
            )
        data = resp.json() if resp.content else {}
        if data.get("errcode"):
            raise DingTalkError(
                data.get("errmsg") or "user/get 失败",
                api="topapi/v2/user/get",
                http_status=resp.status_code,
            )
        return list((data.get("result") or {}).get("dept_id_list") or [])

    # ---- 通讯录（拉全公司的人，给"钉钉号 ↔ CRM 账号"对上用）----------------
    #
    # 钉钉**没有**"给我全公司的人"这一个接口，只能从根部门（id = 1）开始
    # 逐层取子部门、再取每个部门的成员，自己去重 —— 同一个人可以在多个部门里出现。
    # 去重与遍历放在 service 层（那是业务动作），这里只提供两个原子能力。

    def _require_directory(self) -> None:
        """人员绑定同时服务登录与审批；任一业务已开启时可读取通讯录。"""
        if not settings.dingtalk_login_off:
            self._require_login()
        else:
            self._require()

    async def list_sub_departments(self, dept_id: int) -> list[dict[str, Any]]:
        """列某个部门下的**直接**子部门（根部门 id 固定为 1）。

        参数名是 `dept_id`（下划线），结果在 `result` 里 —— 与 `topapi/v2/user/list`
        同一套写法（这组旧版接口用的是这个形制，别按新版的 `deptId` 写）。

        人员绑定同时服务扫码登录和审批发起；两项功能任一开启即可读取。
        """
        self._require_directory()
        token = await self.access_token(for_login=not settings.dingtalk_login_off)
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                "https://oapi.dingtalk.com/topapi/v2/department/listsub",
                params={"access_token": token},
                json={"dept_id": dept_id},
            )
        data = resp.json() if resp.content else {}
        if data.get("errcode"):
            raise DingTalkError(
                data.get("errmsg") or "department/listsub 失败",
                api="topapi/v2/department/listsub",
                http_status=resp.status_code,
            )
        return list(data.get("result") or [])

    async def list_department_users(
        self, dept_id: int, *, cursor: int = 0, size: int = 100
    ) -> dict[str, Any]:
        """列某个部门的**直接**成员，返回原样的 `result`（要按 cursor 翻页）。

        返回 `{list: [...], has_more: bool, next_cursor: int}`，每个人的字段里
        有 `userid` / `name` / `mobile` / `unionid`（`mobile` 需要有通讯录手机号权限）。

        翻页的收尾判据是 **`has_more`**，不是"返回条数 < size"：钉钉最后一页
        也可能给满 100 条并把 `has_more` 置 false，按条数判会多要一次空页。
        `size` 上限 100，传大了接口会拒。

        人员绑定的开关口径与 `list_sub_departments` 相同。
        """
        self._require_directory()
        token = await self.access_token(for_login=not settings.dingtalk_login_off)
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                "https://oapi.dingtalk.com/topapi/v2/user/list",
                params={"access_token": token},
                json={"dept_id": dept_id, "cursor": cursor, "size": size},
            )
        data = resp.json() if resp.content else {}
        if data.get("errcode"):
            raise DingTalkError(
                data.get("errmsg") or "user/list 失败",
                api="topapi/v2/user/list",
                http_status=resp.status_code,
            )
        return dict(data.get("result") or {})

    # ---- 扫码登录（"用钉钉扫一下就能进系统"）--------------------------------
    #
    # 与上面那些"以**企业**身份去操作钉钉"的能力不同，扫码登录是"以**用户**身份
    # 问钉钉：正在扫码的这个人是谁"。三步：授权码 → 用户级令牌 → 用户信息（unionId），
    # 最后再拿 unionId 换 userid（系统里绑定的是 userid）。
    #
    # 为什么最后一步要绕回**旧版**接口：新版 OAuth2 只给 unionId，没有 userid，
    # 而 `users.dingtalk_userid` 存的、通讯录同步用的都是 userid，必须换。
    #
    # 铁律（照 `auth/router.py::wecom_sso_callback` 的教训）：**身份只能由服务端
    # 用 code 换取**，请求体里自报的 userid/unionid 一律不认。

    def build_login_url(self, *, redirect_uri: str, state: str) -> str:
        """拼「钉钉扫码登录」的授权地址（**纯拼字符串，不发任何外部请求**）。

        故意**不**走 `_require()`：这一步只是把用户送到钉钉页面，真正的对外调用
        发生在用户扫完码之后（`exchange_user_token`）。闸门关着也允许先送过去，
        用户到那边会看到明确失败原因，比"按钮点了没反应"强。
        """
        query = urlencode(
            {
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "client_id": settings.dingtalk_app_key,
                "scope": "openid",
                "state": state,
                "prompt": "consent",
            }
        )
        return f"https://login.dingtalk.com/oauth2/auth?{query}"

    async def exchange_user_token(self, code: str) -> str:
        """用一次性的登录授权码换**用户级**访问令牌。

        `grantType` 固定 `authorization_code`。换回来的令牌只能代表"这个人"，
        拿去调通讯录/审批会被拒（那些要用企业内部令牌）。
        授权码**只能用一次**，重复换会失败——本身即一道防重放。
        """
        self._require_login()
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    f"{settings.dingtalk_base_url}/v1.0/oauth2/userAccessToken",
                    json={
                        "clientId": settings.dingtalk_app_key,
                        "clientSecret": settings.dingtalk_app_secret,
                        "code": code,
                        "grantType": "authorization_code",
                    },
                )
        except httpx.HTTPError as exc:
            raise DingTalkError(
                f"换取用户令牌失败：{exc}", api="oauth2/userAccessToken"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise DingTalkError(
                "用户令牌响应不是合法 JSON",
                api="oauth2/userAccessToken",
                http_status=resp.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise DingTalkError(
                f"用户令牌响应结构错误：{type(data).__name__}",
                api="oauth2/userAccessToken",
                http_status=resp.status_code,
            )
        if resp.status_code != 200 or not data.get("accessToken"):
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="oauth2/userAccessToken",
                http_status=resp.status_code,
            )
        return str(data["accessToken"])

    async def get_login_userinfo(self, user_access_token: str) -> dict[str, Any]:
        """用用户级令牌取「本人是谁」（返回 unionId / nick / avatarUrl 等）。

        ⚠️ 这里**只有 unionId**，没有 userid、也没有手机号——扫码登录拿不到企业信息，
        这是钉钉的设计（见官方"扫码登录第三方网站"）。要 userid 得再换一次。
        """
        self._require_login()
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.get(
                    f"{settings.dingtalk_base_url}/v1.0/contact/users/me",
                    headers={"x-acs-dingtalk-access-token": user_access_token},
                )
        except httpx.HTTPError as exc:
            raise DingTalkError(
                f"读取钉钉用户信息失败：{exc}", api="contact/users/me"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise DingTalkError(
                "钉钉用户信息响应不是合法 JSON",
                api="contact/users/me",
                http_status=resp.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise DingTalkError(
                f"钉钉用户信息响应结构错误：{type(data).__name__}",
                api="contact/users/me",
                http_status=resp.status_code,
            )
        if resp.status_code != 200 or not data.get("unionId"):
            raise DingTalkError(
                data.get("message") or resp.text[:200],
                api="contact/users/me",
                http_status=resp.status_code,
            )
        return data

    async def userid_by_unionid(self, unionid: str) -> str | None:
        """unionId → userid（旧版接口，新版没有等价能力）。

        查不到返回 None（如 33002「查无此人」）——那是"这人不在企业通讯录里"，
        不是"系统坏了"，由调用方翻成人话，而不是瞎猜一个 userid。
        """
        self._require_login()
        token = await self.access_token(for_login=True)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    "https://oapi.dingtalk.com/topapi/user/getbyunionid",
                    params={"access_token": token},
                    json={"unionid": unionid},
                )
        except httpx.HTTPError as exc:
            raise DingTalkError(
                f"查询钉钉成员失败：{exc}", api="topapi/user/getbyunionid"
            ) from exc
        try:
            data = resp.json() if resp.content else {}
        except ValueError as exc:
            raise DingTalkError(
                "钉钉成员查询响应不是合法 JSON",
                api="topapi/user/getbyunionid",
                http_status=resp.status_code,
            ) from exc
        if not isinstance(data, dict):
            raise DingTalkError(
                f"钉钉成员查询响应结构错误：{type(data).__name__}",
                api="topapi/user/getbyunionid",
                http_status=resp.status_code,
            )
        if data.get("errcode"):
            return None
        return (data.get("result") or {}).get("userid") or None


    async def get_login_contact_user(self, user_id: str) -> dict[str, Any]:
        """登录用企业成员详情；只走登录开关，与 OA 审批开关独立。"""
        self._require_login()
        token = await self.access_token(for_login=True)
        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    "https://oapi.dingtalk.com/topapi/v2/user/get",
                    params={"access_token": token}, json={"userid": user_id},
                )
            data = resp.json() if resp.content else {}
        except (httpx.HTTPError, ValueError) as exc:
            raise DingTalkError("读取企业成员详情失败，请重试", api="topapi/v2/user/get") from exc
        if not isinstance(data, dict) or not isinstance(data.get("result", {}), dict):
            raise DingTalkError("企业成员详情响应格式无效", api="topapi/v2/user/get")
        if resp.status_code != 200 or data.get("errcode"):
            raise DingTalkError(data.get("errmsg") or "读取企业成员详情失败",
                                api="topapi/v2/user/get", http_status=resp.status_code)
        return dict(data.get("result") or {})

_client: DingTalkClient | None = None


def get_client() -> DingTalkClient:
    global _client
    if _client is None:
        _client = DingTalkClient()
    return _client
