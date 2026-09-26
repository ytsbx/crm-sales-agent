"""企业微信事件回调的签名校验与消息解密（API §10 POST /webhooks/wecom/events）。

企微的规矩（官方「加解密方案说明」）：
1. 回调 URL 校验：企微带 `msg_signature / timestamp / nonce / echostr` GET 过来，
   我们要校验签名、解密 echostr，并**原样返回明文**才算配置成功；
2. 业务事件：POST 一个 XML，其中 `<Encrypt>` 是密文，同样先校验签名再解密；
3. 签名 = sha1(sorted(token, timestamp, nonce, encrypt))；
4. 加密是 AES-256-CBC，key = base64decode(EncodingAESKey + "=")，
   IV 取 key 前 16 字节；明文结构为 `random(16) + msg_len(4, 大端) + msg + receiveid`。

这里刻意不引第三方企微 SDK：加解密本身不到 50 行，自己写能把
"为什么校验失败"讲清楚（签名不对 / 明文里的 receiveid 不匹配），
出了线上问题不用去猜 SDK 内部做了什么。
"""

from __future__ import annotations

import base64
import hashlib
import struct
from typing import Any
from xml.etree import ElementTree

from Crypto.Cipher import AES

from app.core.config import settings


class WeComCallbackError(RuntimeError):
    """回调校验或解密失败。"""


def _aes_key() -> bytes:
    if not settings.wecom_callback_aes_key:
        raise WeComCallbackError("还没配置 WECOM_CALLBACK_AES_KEY")
    key = base64.b64decode(settings.wecom_callback_aes_key + "=")
    if len(key) != 32:
        raise WeComCallbackError("EncodingAESKey 长度不对，应为 43 位 base64 字符")
    return key


def compute_signature(*, token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """sha1 对四个值排序后拼接——顺序必须按字典序排，不是按参数顺序。"""
    raw = "".join(sorted([token, timestamp, nonce, encrypt]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def verify_signature(*, signature: str, timestamp: str, nonce: str, encrypt: str) -> None:
    expected = compute_signature(
        token=settings.wecom_callback_token,
        timestamp=timestamp,
        nonce=nonce,
        encrypt=encrypt,
    )
    if expected != signature:
        raise WeComCallbackError("回调签名校验失败：token 或参数不对")


def _unpad(data: bytes) -> bytes:
    pad = data[-1]
    if pad < 1 or pad > 32:
        pad = 0
    return data[:-pad] if pad else data


def decrypt(encrypt: str) -> str:
    """解密并校验 receiveid，返回明文（XML 字符串）。"""
    key = _aes_key()
    try:
        raw = base64.b64decode(encrypt)
        cipher = AES.new(key, AES.MODE_CBC, key[:16])
        plain = _unpad(cipher.decrypt(raw))
    except Exception as error:  # 解密失败的原因对排查很关键，别吞掉
        raise WeComCallbackError(f"回调消息解密失败：{error}") from error

    # 结构：random(16) + msg_len(4) + msg + receiveid
    if len(plain) < 20:
        raise WeComCallbackError("回调消息长度异常")
    msg_len = struct.unpack("!I", plain[16:20])[0]
    message = plain[20 : 20 + msg_len].decode("utf-8")
    receiveid = plain[20 + msg_len :].decode("utf-8")
    if settings.wecom_corp_id and receiveid != settings.wecom_corp_id:
        raise WeComCallbackError("回调消息的 receiveid 与本企业 corp id 不一致")
    return message


def parse_event(xml_text: str) -> dict[str, Any]:
    """把事件 XML 转成扁平字典。

    企微事件 XML 是一层字段加一层嵌套（如 `<ExternalContact><UserID>`），
    这里只摊平两层：同步任务里要用的字段都在这一层内。
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as error:
        raise WeComCallbackError(f"回调 XML 解析失败：{error}") from error

    payload: dict[str, Any] = {}
    for child in root:
        if len(child):
            payload[child.tag] = {sub.tag: (sub.text or "") for sub in child}
        else:
            payload[child.tag] = child.text or ""
    return payload
