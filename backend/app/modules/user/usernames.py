"""首次扫码与个人账号修改共用的登录名分配。"""
import re
from pypinyin import lazy_pinyin
from sqlalchemy import select, text
from app.modules.user.model import User


async def lock_usernames(session):
    try:
        dialect = session.get_bind().dialect.name
    except AttributeError:
        dialect = ""
    if dialect == "postgresql":
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('crm:username-allocation', 0))"))


def pinyin_username(name):
    value = re.sub(r"[^a-z0-9_]", "", "".join(lazy_pinyin(name)).lower())
    if not value or not value[0].isalpha():
        value = "user" + value
    return value[:54]


async def allocate_username(session, name):
    await lock_usernames(session)
    used = {value.lower() for value in (await session.execute(select(User.username))).scalars()}
    base = pinyin_username(name)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}{suffix}"
        suffix += 1
    return candidate
