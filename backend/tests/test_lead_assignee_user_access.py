"""员工选人和用户管理共用列表，按权限返回不同字段。仅内存 SQLite。"""

import httpx
import pytest
from fastapi import FastAPI

from app.core.jsonio import DecimalJSONResponse
from sqlalchemy.orm import Session

from conftest import SyncSessionAsAsync, make_engine, make_user
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import register_exception_handlers
from app.modules.user.model import Role, User, user_roles
from app.modules.user.router import router


@pytest.fixture
def isolated_app():
    engine = make_engine()
    Role.__table__.create(engine)
    user_roles.create(engine)
    with Session(engine) as raw:
        raw.add_all([
            User(id=i, name=f'候选员工{i}', username=f'candidate{i}', status='active', password_hash='isolated-not-login',
                 mobile=f'isolated-phone-{i}', email=f'candidate{i}@example.invalid',
                 wecom_userid=f'isolated-wecom-{i}', dingtalk_userid=f'isolated-ding-{i}')
            for i in range(1, 22)
        ])
        raw.add(Role(id=1, code='isolated-role', name='隔离角色', data_scope='self', status='active'))
        raw.flush()
        raw.get(User, 21).oa_user_id = 'isolated-oa-21'
        raw.execute(user_roles.insert().values(user_id=21, role_id=1))
        raw.commit()
        app = FastAPI(default_response_class=DecimalJSONResponse)
        app.include_router(router)
        register_exception_handlers(app)
        async def session_override():
            yield SyncSessionAsAsync(raw)
        app.dependency_overrides[get_db] = session_override
        yield app
    engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize('permission', ['lead:assign', 'customer:assign'])
async def test_assignment_can_search_and_page_only_selection_fields(isolated_app, permission):
    async def current():
        return make_user(1, permissions=(permission,))
    isolated_app.dependency_overrides[get_current_user] = current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url='http://isolated') as client:
        first = await client.get('/users', params={'page': 1, 'page_size': 20})
        assert first.status_code == 200
        assert first.json()['data']['total'] == 21
        assert len(first.json()['data']['items']) == 20
        second = await client.get('/users', params={'page': 2, 'page_size': 20})
        assert [item['id'] for item in second.json()['data']['items']] == [21]
        for keyword in ('候选员工21', 'candidate21'):
            result = await client.get('/users', params={'keyword': keyword, 'page_size': 20})
            assert result.status_code == 200
            items = result.json()['data']['items']
            assert [item['id'] for item in items] == [21]
            assert items == [{
                'id': 21,
                'name': '候选员工21',
                'username': 'candidate21',
                'department_id': None,
                'department': None,
                'status': 'active',
            }]
        assert (await client.get('/users/21')).status_code == 403
        # 查询授权不连带开放用户管理的写权限。
        forbidden = await client.post('/users', json={'username': 'forbidden', 'name': '越权新用户', 'password': 'isolated-only'})
        assert forbidden.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize('permissions', [(), ('lead:view',), ('file:manage',)])
async def test_other_permissions_do_not_gain_user_list_access(isolated_app, permissions):
    async def current():
        return make_user(1, permissions=permissions)
    isolated_app.dependency_overrides[get_current_user] = current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url='http://isolated') as client:
        response = await client.get('/users')
    assert response.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize('permissions,roles', [
    (('user:manage',), ('salesperson',)),
    ((), ('admin',)),
])
async def test_user_management_keeps_full_directory_fields(isolated_app, permissions, roles):
    async def current():
        return make_user(1, permissions=permissions, roles=roles)
    isolated_app.dependency_overrides[get_current_user] = current
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=isolated_app), base_url='http://isolated') as client:
        response = await client.get('/users', params={'keyword': 'candidate21', 'status': 'active', 'page_size': 1})
    assert response.status_code == 200
    full, = response.json()['data']['items']
    assert full['mobile'] == 'isolated-phone-21'
    assert full['email'] == 'candidate21@example.invalid'
    assert full['wecom_userid'] == 'isolated-wecom-21'
    assert full['dingtalk_userid'] == 'isolated-ding-21'
    assert [role['code'] for role in full['roles']] == ['isolated-role']
    assert 'password_hash' not in full
    if 'admin' in roles:
        assert full['oa_user_id'] == 'isolated-oa-21'
    else:
        assert 'oa_user_id' not in full
