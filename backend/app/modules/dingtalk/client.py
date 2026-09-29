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
        if resp.status_code != 200 or not data.get("instanceId"):
            raise DingTalkError(
                data.get("message") or resp.text[:200], api="workflow/processInstances:get"
            )
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
            )
        return data.get("result") or data


_client: DingTalkClient | None = None


def get_client() -> DingTalkClient:
    global _client
    if _client is None:
        _client = DingTalkClient()
    return _client
