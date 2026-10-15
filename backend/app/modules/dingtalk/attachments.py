"""审批附件：官方 workflow/storage 1.0 SDK 契约，初始化→签名 PUT→提交。

图片 media_id 与附件的 spaceId/fileId 是两套协议，不能混用。
"""
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import settings
from app.modules.dingtalk.client import DingTalkError


async def upload_approval_attachment(client, *, user_id: str, filename: str, content: bytes) -> dict:
    client._require()
    token = await client.access_token()
    headers = {"x-acs-dingtalk-access-token": token}
    base = settings.dingtalk_base_url

    async with httpx.AsyncClient(timeout=40, follow_redirects=False) as http:
        async def post(path: str, body: dict, params=None) -> dict:
            resp = await http.post(base + path, headers=headers, json=body, params=params)
            try:
                data = resp.json()
            except ValueError as exc:
                raise DingTalkError("附件接口响应无法解析", api=path, http_status=resp.status_code) from exc
            if resp.status_code != 200 or not isinstance(data, dict) or data.get("success") is False:
                raise DingTalkError("附件上传被拒绝，请管理员核对审批附件及钉盘接口权限", api=path, http_status=resp.status_code)
            return data

        # storage 接口使用 unionId；必须由本次发起人的真实 userid 查询。
        resp = await http.post("https://oapi.dingtalk.com/topapi/v2/user/get",
                               params={"access_token": token}, json={"userid": user_id})
        data = resp.json()
        union_id = (data.get("result") or {}).get("unionid")
        if resp.status_code != 200 or data.get("errcode") or not union_id:
            raise DingTalkError("无法取得发起人的钉钉身份，未提交附件或审批", api="topapi/v2/user/get")
        body = {"userId": user_id}
        if settings.dingtalk_agent_id:
            body["agentId"] = int(settings.dingtalk_agent_id)
        data = await post("/v1.0/workflow/processInstances/spaces/infos/query", body)
        space_id = (data.get("result") or {}).get("spaceId")
        if not space_id:
            raise DingTalkError("未取得审批附件空间", api="workflow/spaces/infos/query")
        path = f"/v1.0/storage/spaces/{quote(str(space_id), safe='')}/files"
        info = await post(path + "/uploadInfos/query", {"protocol": "HEADER_SIGNATURE", "multipart": False},
                          {"unionId": union_id})
        signed = info.get("headerSignatureInfo") or {}
        urls = signed.get("resourceUrls") or []
        upload_key = info.get("uploadKey")
        if len(urls) != 1 or not upload_key:
            raise DingTalkError("附件上传信息不完整", api="storage/files/uploadInfos/query")
        url = urlsplit(urls[0])
        host = (url.hostname or "").lower()
        if url.scheme != "https" or url.username or url.password or not any(
            host.endswith(suffix) for suffix in (".aliyuncs.com", ".dingtalk.com")
        ):
            raise DingTalkError("附件上传地址不受信任", api="storage/files/uploadInfos/query")
        # 只向钉钉给出的签名地址发送其签名头，不附带 access_token，也不跟随重定向。
        uploaded = await http.put(urls[0], headers=signed.get("headers") or {}, content=content)
        if uploaded.status_code not in (200, 201, 204):
            raise DingTalkError("附件传输失败，尚未发起审批", api="storage/signed-put", http_status=uploaded.status_code)
        committed = await post(path + "/commit", {"name": filename, "parentId": "0", "uploadKey": upload_key},
                               {"unionId": union_id})
        file = committed.get("dentry") or {}
        if not file.get("id") or str(file.get("spaceId")) != str(space_id) or file.get("size") != len(content):
            raise DingTalkError("附件入库结果不完整或文件大小不符，尚未发起审批", api="storage/files/commit")
        return {"spaceId": str(space_id), "fileId": str(file["id"]), "fileName": file.get("name") or filename,
                "fileSize": len(content), "fileType": file.get("extension") or Path(filename).suffix.lstrip(".")}
