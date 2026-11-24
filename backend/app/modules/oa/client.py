"""CRM → 自研 OA 的服务端客户端。

OA 侧接口只认 HMAC 签名（共享密钥 + 时间戳），不使用任何用户密码/token。
请求体必须**按签名的同一串字节**发送；JSON 用紧凑序列化，避免签名与发送字节不一致。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import time
import uuid

import httpx

from app.core.config import settings


class OaError(Exception):
    """OA 明确拒绝（4xx）或业务错误；调用方可原样把 message 给人看。"""

    def __init__(self, message: str, *, code: str = "oa_error", status: int = 502):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


class OaNotConfigured(OaError):
    def __init__(self, message: str):
        super().__init__(message, code="oa_not_configured", status=422)


class OaUnknownOutcome(OaError):
    """超时/连接断/5xx：OA 那边可能已经建单，不能当作“没发出去”。"""

    def __init__(self, message: str):
        super().__init__(message, code="oa_unknown_outcome", status=502)


def approval_external_ref(kind: str, business_id: int, round_no: int) -> str:
    reference = f"crm-{kind}:{business_id}:{round_no}"
    namespace = settings.oa_external_ref_namespace
    return f"{namespace}:{reference}" if namespace else reference


def configured() -> bool:
    return bool(settings.oa_base_url and settings.oa_integration_secret)


def applicant_identity(account) -> uuid.UUID | str | None:
    oa_user_id = getattr(account, "oa_user_id", None)
    return uuid.UUID(oa_user_id) if oa_user_id else getattr(account, "dingtalk_userid", None)


def applicant_fields(identity: uuid.UUID | str) -> dict[str, str]:
    key = "applicant_oa_user_id" if isinstance(identity, uuid.UUID) else "applicant_dingtalk_user_id"
    return {key: str(identity)}


def owns_approval(account, row) -> bool:
    creator_id = getattr(row, "created_by", None)
    if creator_id is not None:
        return account is not None and account.id == creator_id
    return bool(account and row.originator_user_id and row.originator_user_id in {
        getattr(account, "oa_user_id", None), getattr(account, "dingtalk_userid", None),
    })


def _path_with_query(path: str, params: dict | None) -> str:
    if not params:
        return path
    from urllib.parse import urlencode
    return f"{path}?{urlencode(params)}"


def _signature(timestamp: str, method: str, path: str, body: bytes, *, secret: str | None = None) -> str:
    canonical = "\n".join(
        [timestamp, method.upper(), path, hashlib.sha256(body or b"").hexdigest()]
    )
    return hmac.new(
        (secret if secret is not None else settings.oa_integration_secret).encode(), canonical.encode(), hashlib.sha256
    ).hexdigest()


async def _request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    content_type: str | None = None,
    params: dict | None = None,
    connection: tuple[str, str] | None = None,
) -> dict:
    base_url, secret = connection or (settings.oa_base_url, settings.oa_integration_secret)
    if not base_url or not secret:
        raise OaNotConfigured("还没配置自研 OA 对接地址或密钥，请联系管理员")
    full_path = _path_with_query(path, params)
    # OA 用 request.get_full_path() 签名，路径含 /api/v1/integrations/crm 前缀；
    # 客户端必须签同一段路径，否则 401「签名校验失败」。
    signed_path = "/api/v1/integrations/crm" + full_path
    payload = body or b""
    timestamp = str(int(time.time()))
    headers = {
        "X-CRM-Timestamp": timestamp,
        "X-CRM-Signature": _signature(timestamp, method, signed_path, payload, secret=secret),
    }
    if content_type:
        headers["Content-Type"] = content_type
    url = base_url.rstrip("/") + "/api/v1/integrations/crm" + full_path
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.request(method, url, content=payload, headers=headers)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        raise OaUnknownOutcome(f"OA 请求没有得到明确结果：{exc.__class__.__name__}") from exc
    if response.status_code >= 500:
        raise OaUnknownOutcome(f"OA 返回 {response.status_code}，结果未知")
    try:
        data = response.json()
    except ValueError as exc:
        if method == "POST" and path == "/approvals/" and response.is_success:
            raise OaUnknownOutcome("OA 可能已有审批单，但发起回执无法解析") from exc
        if method == "POST" and path.endswith("/withdraw/") and response.is_success:
            raise OaUnknownOutcome("OA 撤回回执无法解析") from exc
        raise OaError("OA 返回了无法解析的响应", status=502) from exc
    if response.status_code >= 400:
        raise OaError(
            str(data.get("message") or f"OA 拒绝了请求（{response.status_code}）"),
            code=str(data.get("code") or "oa_error"),
            status=response.status_code,
        )
    return data


def _json_body(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()


async def create_approval(payload: dict) -> dict:
    return await _request(
        "POST", "/approvals/", body=_json_body(payload), content_type="application/json"
    )


def _existing_connection(approval_id: str) -> dict:
    ids = {value.strip() for value in settings.oa_legacy_instance_ids.split(',') if value.strip()}
    if approval_id not in ids:
        return {}
    if not settings.oa_legacy_base_url or not settings.oa_legacy_integration_secret:
        raise OaNotConfigured("历史审批连接未配置，请联系管理员")
    return {'connection': (settings.oa_legacy_base_url, settings.oa_legacy_integration_secret)}


async def get_approval(approval_id: str) -> dict:
    return await _request("GET", f"/approvals/{approval_id}/", **_existing_connection(approval_id))


async def withdraw_approval(approval_id: str, note: str) -> dict:
    return await _request(
        "POST",
        f"/approvals/{approval_id}/withdraw/",
        body=_json_body({"note": note}),
        content_type="application/json",
        **_existing_connection(approval_id),
    )


def _multipart(fields: dict[str, str], file_name: str, file_bytes: bytes, mime: str) -> tuple[bytes, str]:
    boundary = f"----crmoa{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{file_name}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode()
    )
    chunks.append(file_bytes)
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


async def upload_file(dingtalk_user_id: uuid.UUID | str, file_name: str, file_bytes: bytes, mime: str | None = None) -> dict:
    mime = mime or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    body, content_type = _multipart(
        applicant_fields(dingtalk_user_id), file_name, file_bytes, mime
    )
    return await _request("POST", "/files/", body=body, content_type=content_type)


async def list_templates(dingtalk_user_id: uuid.UUID | str) -> list[dict]:
    data = await _request(
        "GET", "/templates/", params=applicant_fields(dingtalk_user_id)
    )
    return list(data.get("results") or [])


async def list_directory(applicant_dingtalk_user_id: str | None = None) -> dict:
    """同序的只读人员/部门目录（企业、部门树、在职人员及钉钉身份）。

    建账号、补部门以同序目录为准。
    """
    params = ({"applicant_dingtalk_user_id": applicant_dingtalk_user_id}
              if applicant_dingtalk_user_id else None)
    return await _request("GET", "/directory/", params=params)
