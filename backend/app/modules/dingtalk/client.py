"""钉钉开放平台客户端。

只做三件事：取 token（带缓存）、发起审批实例、查询实例状态。

**关于"未配置"**：与 `ErpNotConfigured` / `WeComNotConfigured` 同一套做法——
缺配置直接抛，接口层翻成明确说明。绝不返回"成功"然后什么都没做，
那种假成功在联调时最费时间（看着通了，实际一条单没建）。

**最容易踩的坑**：钉钉的 `formValues` 用**控件 id** 作 key，不是控件名称。
名字对不上时钉钉不会报错，只会把那一格留空——于是"预填成功"的假象下
业务还得手填一遍，正好把"免重复录入"这条验收标准踩没。所以模板的
字段清单（控件 id + 类型）是联调前必须拿到的输入。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import settings

#: access_token 有效期 7200 秒；提前 5 分钟判过期，避免卡在过期那一刻的请求失败
_TOKEN_SKEW_SECONDS = 300


class DingTalkError(RuntimeError):
    """钉钉接口返回了业务错误。"""

    def __init__(self, message: str, *, api: str, code: str | None = None) -> None:
        self.api = api
        self.code = code
        super().__init__(f"钉钉接口 {api} 返回错误：{message}")


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

    @property
    def configured(self) -> bool:
        return bool(settings.dingtalk_app_key and settings.dingtalk_app_secret)

    def _require(self) -> None:
        if not settings.dingtalk_app_key:
            raise DingTalkNotConfigured("DINGTALK_APP_KEY")
        if not settings.dingtalk_app_secret:
            raise DingTalkNotConfigured("DINGTALK_APP_SECRET")

    async def access_token(self) -> str:
        """企业内部应用 access_token，带进程内缓存。"""
        self._require()
        now = time.monotonic()
        if self._token and now < self._token_expires_at:
            return self._token
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                f"{settings.dingtalk_base_url}/v1.0/oauth2/accessToken",
                json={
                    "appKey": settings.dingtalk_app_key,
                    "appSecret": settings.dingtalk_app_secret,
                },
            )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("accessToken"):
            raise DingTalkError(
                data.get("message") or resp.text[:200], api="oauth2/accessToken"
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
        form_values: list[dict[str, Any]],
        originator_user_id: str,
        dept_id: int | None = None,
    ) -> str:
        """发起审批实例，返回 instance_id（场景11：从 CRM 发起询价审批）。"""
        self._require()
        if not process_code:
            raise DingTalkNotConfigured("审批模板 process_code")
        token = await self.access_token()
        body: dict[str, Any] = {
            "processCode": process_code,
            "originatorUserId": originator_user_id,
            "formValues": form_values,
        }
        if dept_id is not None:
            body["deptId"] = dept_id
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.post(
                f"{settings.dingtalk_base_url}/v1.0/workflow/processInstances",
                json=body,
                headers={"x-acs-dingtalk-access-token": token},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200 or not data.get("instanceId"):
            raise DingTalkError(
                data.get("message") or resp.text[:200], api="workflow/processInstances"
            )
        return data["instanceId"]

    async def get_process_instance(self, instance_id: str) -> dict[str, Any]:
        """查审批实例状态与结果（回写用；没有回调时靠轮询它）。"""
        self._require()
        token = await self.access_token()
        async with httpx.AsyncClient(timeout=15) as http:
            resp = await http.get(
                f"{settings.dingtalk_base_url}/v1.0/workflow/processInstances",
                params={"processInstanceId": instance_id},
                headers={"x-acs-dingtalk-access-token": token},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            raise DingTalkError(
                data.get("message") or resp.text[:200], api="workflow/processInstances:get"
            )
        # 钉钉把审批内容是**包在 `result` 里**的，顶层只有 requestId 之类。
        # 早先按顶层 `instanceId` 判断成功，于是正常响应被误报成"接口失败"
        # ——结果是"结果回收"这条链悄悄不工作（审批批完了 CRM 还显示审批中）。
        # 判据改成"里面有没有审批内容"，拿不到就算失败。
        inner = data.get("result") or data
        if not inner.get("title") and not inner.get("status"):
            raise DingTalkError("响应里没有审批内容", api="workflow/processInstances:get")
        return inner

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
                data.get("errmsg") or resp.text[:200], api="media/upload"
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
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                f"{settings.dingtalk_base_url}/v1.0/contact/users/search",
                headers={"x-acs-dingtalk-access-token": token},
                json={"queryWord": name, "offset": 0, "size": 20},
            )
        data = resp.json() if resp.content else {}
        if resp.status_code != 200:
            raise DingTalkError(
                data.get("message") or resp.text[:200], api="contact/users/search"
            )
        users = data.get("list") or []
        # 精确匹配优先：搜"子木"可能带回一串包含这两个字的人
        for uid in users:
            if users and len(users) == 1:
                return str(uid)
        return str(users[0]) if users else None

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
                data.get("errmsg") or "user/get 失败", api="topapi/v2/user/get"
            )
        return list((data.get("result") or {}).get("dept_id_list") or [])


_client: DingTalkClient | None = None


def get_client() -> DingTalkClient:
    global _client
    if _client is None:
        _client = DingTalkClient()
    return _client
