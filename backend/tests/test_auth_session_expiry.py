from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError, ErrorCode
from app.core.security import create_access_token, decode_access_token
from app.modules.auth.model import LoginSession
from app.modules.auth.session import issue_session_token, load_active_session, open_session
from app.modules.user.model import User
from tests.conftest import SyncSessionAsAsync, make_engine


@pytest.fixture
def db():
    engine = make_engine()
    LoginSession.__table__.create(engine)
    with Session(engine) as raw:
        raw.add(User(id=1, username="session-expiry-test", name="测试", password_hash="x", status="active"))
        raw.flush()
        yield SyncSessionAsAsync(raw)
    engine.dispose()


@pytest.mark.anyio
async def test_session_deadline_caps_tokens_and_rejects_remaining_legacy_tokens(db):
    row = await open_session(db, 1)
    legacy = decode_access_token(create_access_token(1, session_id=row.sid))
    await load_active_session(db, row.sid, 1, token_id=legacy["jti"])

    deadline = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)
    row.created_at = deadline - timedelta(days=settings.login_session_max_days)
    claims = decode_access_token(issue_session_token(row, "测试"))
    await db.flush()
    assert claims["exp"] == int(deadline.timestamp())
    assert claims["sid"] == legacy["sid"] and claims["jti"] != legacy["jti"]
    with pytest.raises(AppError) as old:
        await load_active_session(db, row.sid, 1, token_id=legacy["jti"], for_refresh=True)
    assert old.value.code == ErrorCode.UNAUTHORIZED

    row.created_at -= timedelta(minutes=2)
    await db.flush()
    for for_refresh in (False, True):
        with pytest.raises(AppError) as expired:
            await load_active_session(db, row.sid, 1, token_id=claims["jti"], for_refresh=for_refresh)
        assert expired.value.code == ErrorCode.TOKEN_EXPIRED
