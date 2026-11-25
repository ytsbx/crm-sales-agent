"""登录回调域名白名单：钉钉扫码登录不接受伪造 Host（B11-11）。

## 问题

`auth/router._external_base` 用请求头 Host 拼出"这个站的外网地址"：

- `GET /auth/dingtalk/authorize` 把它拼进 redirect_uri 交给钉钉；
- `GET /auth/dingtalk/callback` 用它拼"登录完跳回哪里"的 Location。

Host 是客户端可控的。若反代没有固定 Host（或以后加了默认站点、开了 IP 直连），
攻击者可以让请求带 `Host: evil.com`，钉钉就把浏览器带着一次性凭证送回 evil.com
—— 开放重定向 / 钓鱼。钉钉只认登记过的回调域名是外部保险，但应用自己不该把
这件事全押在外部系统和部署配置上。

## 做法

按 `settings.auth_callback_hosts` 的白名单，在两个**会构造回调地址**的入口再守
一道：Host（去掉端口、统一小写）不在名单里直接 400，路由根本不会执行。
名单留空 = 不启用（本地开发 / 测试）；生产必须配置实际对外域名。

反代固定 Host 仍然保留，两道一起用：应用层这道不依赖部署配置，也能被测试钉住。
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.errors import ErrorCode

logger = logging.getLogger(__name__)


def normalize_host(value: str | None) -> str:
    """把 Host / 配置项归一成"主机名"：去协议、去路径、去端口、转小写。

    配置里写 `crm.mandla.cn`、`https://crm.mandla.cn`、`crm.mandla.cn:443`
    都当成同一个主机；请求头的 Host 也可能带端口。
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "//" + raw
    host = urlsplit(raw).hostname or ""
    return host.rstrip(".").lower()


def allowed_callback_hosts() -> set[str]:
    return {
        host
        for item in settings.auth_callback_host_list
        if (host := normalize_host(item))
    }


def _callback_paths() -> frozenset[str]:
    prefix = settings.api_prefix.rstrip("/")
    return frozenset({
        f"{prefix}/auth/dingtalk/authorize",
        f"{prefix}/auth/dingtalk/callback",
    })


async def auth_callback_host_guard(request: Request, call_next):
    """只拦"会拿 Host 拼地址"的两个登录入口；白名单为空时完全放行。"""
    if request.url.path in _callback_paths():
        hosts = allowed_callback_hosts()
        if hosts:
            host = normalize_host(request.headers.get("host"))
            if host not in hosts:
                logger.warning(
                    "登录回调域名不在白名单：host=%r path=%s",
                    host,
                    request.url.path,
                )
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": ErrorCode.PARAM_ERROR,
                        "message": "登录回调域名不在白名单，请从正式域名访问",
                        "data": None,
                    },
                )
    return await call_next(request)
