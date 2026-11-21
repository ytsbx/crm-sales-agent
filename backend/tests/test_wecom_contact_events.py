import base64
import struct
import time

import httpx
import pytest
from Crypto.Cipher import AES
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import register_exception_handlers
from app.modules.customer.model import Customer
from app.modules.wecom import callback, router, service
from app.modules.wecom.client import WeComClient, WeComError
from app.modules.wecom.model import WeComExternalContact, WeComFollowRelationship, WeComSyncJob
from tests.conftest import SyncSessionAsAsync, make_engine


@pytest.fixture
def event_db():
    engine = make_engine()
    for model in (WeComSyncJob, WeComExternalContact, WeComFollowRelationship):
        model.__table__.create(engine)
    with Session(engine) as raw:
        yield raw, SyncSessionAsAsync(raw)
    engine.dispose()


def encrypted_message(message: str) -> str:
    key = bytes(range(32))
    body = message.encode()
    data = bytes(16) + struct.pack("!I", len(body)) + body + settings.wecom_corp_id.encode()
    padding = 32 - len(data) % 32
    return base64.b64encode(AES.new(key, AES.MODE_CBC, key[:16]).encrypt(data + bytes([padding]) * padding)).decode()


def request_args(encrypted: str) -> dict:
    timestamp = str(int(time.time()))
    nonce = "test-nonce"
    signature = callback.compute_signature(token=settings.wecom_callback_token, timestamp=timestamp,
                                           nonce=nonce, encrypt=encrypted)
    return {"msg_signature": signature, "timestamp": timestamp, "nonce": nonce}


@pytest.mark.anyio
async def test_callback_queues_once_and_background_sync_retries(event_db, monkeypatch):
    raw, db = event_db
    monkeypatch.setattr(settings, "wecom_corp_id", "test-corp")
    monkeypatch.setattr(settings, "wecom_callback_token", "")
    monkeypatch.setattr(settings, "wecom_callback_aes_key", base64.b64encode(bytes(range(32))).decode().rstrip("="))
    monkeypatch.setattr(settings, "wecom_external_contact_secret", "isolated-contact-event-key")
    monkeypatch.setattr(service.wecom_client, "_token_cache", {})
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router.router)

    async def database():
        return db

    app.dependency_overrides[get_db] = database
    calls = []
    failure = [True]

    def upstream(request):
        if request.url.path.endswith("/gettoken"):
            return httpx.Response(200, json={"access_token": "isolated-token"})
        assert request.url.path == "/cgi-bin/externalcontact/get"
        assert request.url.params["external_userid"] == "customer-event"
        calls.append(request.url.params.get("cursor"))
        if calls[-1] and failure[0]:
            return httpx.Response(200, json={"errcode": 60020})
        item = {"external_contact": {"external_userid": "customer-event", "name": "企微测试客户"}}
        if calls[-1]:
            return httpx.Response(200, json={**item, "follow_user": [{"userid": "b"}]})
        return httpx.Response(200, json={**item, "follow_user": [{"userid": "a"}], "next_cursor": "page-2"})

    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://crm") as crm,
        httpx.AsyncClient(transport=httpx.MockTransport(upstream), base_url="https://wecom.test") as http,
    ):
        monkeypatch.setattr(service.wecom_client, "get_client", lambda: WeComClient(http=http))
        path = "/webhooks/wecom/events"
        check = encrypted_message("check")  # Uses WeCom padding longer than one AES block.
        response = await crm.get(path, params={**request_args(check), "echostr": check})
        assert response.status_code == 503
        monkeypatch.setattr(settings, "wecom_callback_token", "isolated-callback-token")
        response = await crm.get(path, params={**request_args(check), "echostr": check})
        assert response.status_code == 200 and response.text == "check"
        event = encrypted_message("<xml><Event>change_external_contact</Event><ChangeType>add_external_contact</ChangeType>"
                                  "<ExternalUserID>customer-event</ExternalUserID></xml>")
        for _ in range(2):
            response = await crm.post(path, params=request_args(event), content=f"<xml><Encrypt>{event}</Encrypt></xml>")
            assert response.status_code == 200 and response.text == "success"
        assert calls == []
        job = raw.scalars(select(WeComSyncJob)).one()
        assert job.status == "running"

        await service.sync_contact_events(db)
        assert job.status == "running" and job.fail_count == 1
        assert raw.scalars(select(WeComExternalContact)).all() == []
        failure[0] = False
        await service.sync_contact_events(db)
        await service.sync_contact_events(db)
        contact = raw.scalars(select(WeComExternalContact)).one()
        assert job.status == "success" and job.success_count == 1
        assert contact.normalize_status == "pending" and contact.crm_customer_id is None
        assert {row.wecom_userid for row in raw.scalars(select(WeComFollowRelationship))} == {"a", "b"}
        assert raw.scalars(select(Customer)).all() == []
        assert len(calls) == 6


@pytest.mark.anyio
async def test_explicit_removal_only_retires_named_relation_when_contact_is_gone(event_db, monkeypatch):
    raw, db = event_db
    contact = WeComExternalContact(external_userid="customer-event")
    raw.add(contact)
    raw.flush()
    raw.add_all([
        WeComFollowRelationship(external_contact_id=contact.id, wecom_userid=member)
        for member in ("a", "b")
    ])
    event = {"Event": "change_external_contact", "ChangeType": "del_follow_user",
             "ExternalUserID": "customer-event", "UserID": "a"}
    job = WeComSyncJob(job_type="contact_event", status="running", detail={"event": event})
    raw.add(job)
    raw.commit()

    class MissingContact:
        async def get_external_contact(self, external_id):
            assert external_id == "customer-event"
            raise WeComError(84061, "no external contact relation", "externalcontact/get")

    monkeypatch.setattr(service.wecom_client, "get_client", MissingContact)
    await service.sync_contact_events(db)

    assert job.status == "success"
    states = {row.wecom_userid: row.status for row in raw.scalars(select(WeComFollowRelationship))}
    assert states == {"a": "deleted", "b": "active"}
