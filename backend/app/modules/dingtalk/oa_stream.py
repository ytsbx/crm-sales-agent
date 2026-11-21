"""仅接收审批变更；事件只作为刷新信号，状态及权限仍由钉钉接口核实。

传输使用官方 SDK 的帧协议。自管异步连接，避免 SDK 同步 HTTP 阻塞
FastAPI 事件循环，以及 SDK 将临时 ticket 写入日志。
"""
import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx
import websockets
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.modules.dingtalk.model import OaInstance

logger = logging.getLogger('crm.oa_stream')
EVENTS = {'bpms_instance_change', 'bpms_task_change'}


class OaStream:
    def __init__(self):
        self.task = None
        self.connected = False
        self.last_event_at = None
        self.last_error = None

    async def process_event(self, headers, data):
        kind = headers.get('eventType') or data.get('EventType')
        if kind not in EVENTS:
            return
        instance_id = data.get('processInstanceId')
        if not isinstance(instance_id, str) or not instance_id:
            return
        from app.modules.dingtalk.service import sync_pending_instances
        async with SessionLocal() as session:
            row = (await session.execute(select(OaInstance).where(
                OaInstance.instance_id == instance_id,
                OaInstance.oa_type.in_(('inquiry', 'sample', 'contract')),
            ))).scalar_one_or_none()
            # 不导入其它业务的审批，也不采纳事件直接带来的通过/驳回结论。
            if row is not None:
                if data.get('processCode') and data['processCode'] != row.process_code:
                    return
                result = await sync_pending_instances(session, oa_id=row.id)
                if result.get('disabled') or not result.get('checked'):
                    raise RuntimeError('OA 同步关闭或记录正在被其他请求刷新')
                await session.commit()
                if row.error:
                    raise RuntimeError('钉钉快照查询失败')
        self.last_event_at = datetime.now(UTC).isoformat()
        self.last_error = None

    async def handle_frame(self, socket, frame):
        from dingtalk_stream.frames import AckMessage
        headers = frame.get('headers') or {}
        data = frame.get('data') or '{}'
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError('事件内容格式错误')
        ack = AckMessage()
        ack.headers.message_id = headers.get('messageId')
        ack.headers.content_type = 'application/json'
        ack.message = 'OK'
        ack.data = data
        if frame.get('type') == 'EVENT':
            try:
                await self.process_event(headers, data)
            except Exception as exc:
                # 失败不确认消费，让钉钉重投；详细异常不含事件正文和访问票据。
                self.last_error = type(exc).__name__
                ack.code = AckMessage.STATUS_SYSTEM_EXCEPTION
                ack.message = 'CRM sync temporarily unavailable'
                logger.warning('审批事件处理失败（%s），等待重投及轮询补漏', type(exc).__name__)
        await socket.send(json.dumps(ack.to_dict()))
        return frame.get('type') == 'SYSTEM' and headers.get('topic') == 'disconnect'

    async def run(self):
        delay = 2
        while True:
            try:
                async with httpx.AsyncClient(timeout=15) as http:
                    response = await http.post('https://api.dingtalk.com/v1.0/gateway/connections/open', json={
                        'clientId': settings.dingtalk_app_key,
                        'clientSecret': settings.dingtalk_app_secret,
                        'subscriptions': [{'type': 'EVENT', 'topic': '*'}],
                        'ua': 'crm-oa/1.1', 'localIp': '127.0.0.1',
                    })
                    response.raise_for_status()
                    connection = response.json()
                endpoint = connection.get('endpoint') or ''
                parsed = urlsplit(endpoint)
                if parsed.scheme != 'wss' or not (parsed.hostname or '').endswith('.dingtalk.com') or not connection.get('ticket'):
                    raise ValueError('Stream 网关未返回有效连接')
                uri = endpoint + '?ticket=' + quote(connection['ticket'], safe='')
                async with websockets.connect(uri, open_timeout=15, ping_interval=20, ping_timeout=20) as socket:
                    self.connected = True
                    self.last_error = None
                    delay = 2
                    logger.info('OA Stream 已连接；仅处理 CRM 已关联的审批事件')
                    async for raw in socket:
                        if await self.handle_frame(socket, json.loads(raw)):
                            break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.warning('OA Stream 连接中断（%s），%s 秒后重连；定时同步继续', type(exc).__name__, delay)
            finally:
                self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    def start(self):
        if settings.dingtalk_oa_stream_enabled and not settings.dingtalk_push_off and (self.task is None or self.task.done()):
            self.task = asyncio.create_task(self.run())

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
            self.task = None
        self.connected = False


oa_stream = OaStream()
