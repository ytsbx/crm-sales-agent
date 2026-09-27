"""Agent 流式输出的离线验证（不依赖网络与 DEEPSEEK_API_KEY）。

跑法（后端目录，需要 PostgreSQL 中间件在跑）：
    cd backend
    PYTHONPATH=. .venv/bin/python scripts/check_agent_stream.py

脚本自带清库，可反复执行。

## 验证什么

把 `runtime._client` 换成**伪造的流式客户端**，按 OpenAI 流式协议吐分片：
  1. 文本增量是否按到达顺序逐个 yield（`delta` 事件）；
  2. 工具调用参数**跨分片拼接**是否正确（JSON 拆成三片再拼回来）；
  3. L1 工具执行后 `tool_call` 事件带解析后的入参；
  4. L2 写动作是否照旧落成"待确认"（挂起语义不被流式破坏）；
  5. `done` 的 reply 与落库的 assistant 消息一致；
  6. 同步口径 `run_turn`（POST /messages 用）与流式结果一致。

## 覆盖不到什么

真实模型的节奏、SSE 帧在网络上的到达顺序——那要靠跑着后端
用真 key 实测（见 check_agent_api.py 第 3 节）。
"""

import asyncio
import json
import sys
import time

RUN = str(int(time.time()))[-6:]
FAILURES = []


def check(label, actual, expected):
    good = actual == expected
    print(f'  {"OK  " if good else "FAIL"} {label}: {actual!r}（期望 {expected!r}）')
    if not good:
        FAILURES.append(label)


def check_true(label, condition, detail=''):
    print(f'  {"OK  " if condition else "FAIL"} {label}{f"：{detail}" if detail else ""}')
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------- 伪造流式客户端

class _Function:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, index, id=None, function=None):
        self.index = index
        self.id = id
        self.function = function


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    def __init__(self, delta):
        self.delta = delta


class _Chunk:
    def __init__(self, delta):
        self.choices = [_Choice(delta)]


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    async def _gen(self):
        for chunk in self._chunks:
            await asyncio.sleep(0.001)  # 模拟网络到达间隔
            yield chunk

    def __aiter__(self):
        return self._gen()


class _FakeCompletions:
    """按脚本逐轮吐分片；每轮对应一次 create 调用。"""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._rounds:
            raise AssertionError('脚本分片用完了还有模型调用')
        return _Stream(self._rounds.pop(0))


class _FakeClient:
    def __init__(self, rounds):
        self.chat = type('Chat', (), {})()
        self.chat.completions = _FakeCompletions(rounds)


# ---------------------------------------------------------------- 用例

async def collect_events(session, agent_session, user, text):
    from app.modules.agent import runtime

    events = []
    async for kind, data in runtime.run_turn_events(
        session, agent_session=agent_session, user=user, text=text
    ):
        events.append((kind, data))
    return events


async def clean(verbose=False):
    from sqlalchemy import text

    from app.core.database import SessionLocal

    statements = [
        ('用例动作', "delete from agent_actions where session_id in "
                  f"(select id from agent_sessions where title like '%CHK{RUN}%')"),
        ('用例消息', "delete from agent_messages where session_id in "
                  f"(select id from agent_sessions where title like '%CHK{RUN}%')"),
        ('用例会话', f"delete from agent_sessions where title like '%CHK{RUN}%'"),
    ]
    from app.core.database import SessionLocal
    async with SessionLocal() as s:
        for label, sql in statements:
            result = await s.execute(text(sql))
            if verbose and result.rowcount:
                print(f'  {result.rowcount:>4}  {label}')
        await s.commit()


async def main():
    from unittest.mock import patch

    from sqlalchemy import select

    from app.core.database import SessionLocal
    from app.core.deps import CurrentUser
    from app.modules.agent import runtime
    from app.modules.agent.model import AgentMessage, AgentSession
    from app.modules.user.model import User

    # 真实的 admin 用户行（CurrentUser 只拷贝标量字段，会话关了也能用）
    async with SessionLocal() as bootstrap:
        admin_row = await bootstrap.get(User, 1)
    user = CurrentUser(admin_row, permissions=set(), roles=['admin'], data_scope='all')

    print()
    print('=== 1. 读工具（L1）：delta 逐段产出 + 工具参数跨分片拼接 ===')
    tool_args_fragments = ['{"key', 'word": "不存在的客户', 'XYZ"}']
    final_text = '没有查到叫这个名字的客户，你可以告诉我准确的客户名吗？'
    fake = _FakeClient(rounds=[
        [
            _Chunk(_Delta(content='我先查一下这个客户。')),
            _Chunk(_Delta(tool_calls=[_ToolCall(0, id='call_1', function=_Function(name='search_customers'))])),
            _Chunk(_Delta(tool_calls=[_ToolCall(0, function=_Function(arguments=tool_args_fragments[0]))])),
            _Chunk(_Delta(tool_calls=[_ToolCall(0, function=_Function(arguments=tool_args_fragments[1]))])),
            _Chunk(_Delta(tool_calls=[_ToolCall(0, function=_Function(arguments=tool_args_fragments[2]))])),
        ],
        [
            _Chunk(_Delta(content=final_text[:10])),
            _Chunk(_Delta(content=final_text[10:20])),
            _Chunk(_Delta(content=final_text[20:])),
        ],
    ])

    async with SessionLocal() as session:
        agent_session = AgentSession(user_id=1, title=f'CHK{RUN}流式读')
        session.add(agent_session)
        await session.commit()
        sid = agent_session.id

        with patch.object(runtime, '_client', lambda: fake):
            events = await collect_events(session, agent_session, user, f'CHK{RUN} 查客户')

        kinds = [k for k, _ in events]
        deltas = [d['text'] for k, d in events if k == 'delta']
        tool_events = [d for k, d in events if k == 'tool_call']
        done = dict(events)['done']

        check_true('事件顺序：user_message 在最前', kinds[0] == 'user_message', str(kinds[:3]))
        check_true('含 delta 与 tool_call 事件', 'delta' in kinds and 'tool_call' in kinds, str(set(kinds)))
        check_true('工具调用前先有它的说明文本', deltas and deltas[0] == '我先查一下这个客户。', str(deltas[:1]))
        check_true('delta 按到达顺序产出', deltas[-3:] == [final_text[:10], final_text[10:20], final_text[20:]], str(deltas[-3:]))
        check_true('工具参数跨分片拼接正确',
                   tool_events and tool_events[0]['input'] == {'keyword': '不存在的客户XYZ'},
                   str(tool_events[0]['input'] if tool_events else None))
        check_true('done.reply 是完整回复', done['reply'] == final_text, done['reply'][:30])
        check_true('reply 落库', True, '')  # 与下一条合并验证
        async with SessionLocal() as verify:
            row = (
                await verify.execute(
                    select(AgentMessage)
                    .where(AgentMessage.session_id == sid, AgentMessage.role == 'assistant')
                    .order_by(AgentMessage.id.desc())
                )
            ).scalars().first()
            check_true('assistant 消息已落库且与 reply 一致',
                       row is not None and row.content == final_text,
                       (row.content[:30] if row else '无记录'))

    print()
    print('=== 2. 写动作（L2）：挂起语义不被流式破坏 ===')
    followup_args = json.dumps(
        {'customer_id': 1, 'content': f'CHK{RUN} 流式测试跟进', 'followup_type': '电话'},
        ensure_ascii=False,
    )
    fake2 = _FakeClient(rounds=[
        [
            _Chunk(_Delta(tool_calls=[_ToolCall(0, id='call_2', function=_Function(name='create_followup'))])),
            _Chunk(_Delta(tool_calls=[_ToolCall(0, function=_Function(arguments=followup_args))])),
        ],
        [_Chunk(_Delta(content='好的，我已经准备好这条跟进，等你在界面上确认。'))],
    ])

    async with SessionLocal() as session:
        agent_session = AgentSession(user_id=1, title=f'CHK{RUN}流式写')
        session.add(agent_session)
        await session.commit()

        with patch.object(runtime, '_client', lambda: fake2):
            events = await collect_events(session, agent_session, user, f'CHK{RUN} 记跟进')

        done = dict(events)['done']
        tool_events = [d for k, d in events if k == 'tool_call']
        check_true('L2 不执行、落成待确认动作',
                   tool_events and tool_events[0]['output'].get('status') == 'awaiting_user_confirmation',
                   str(tool_events[0]['output'] if tool_events else None))
        check_true('done.actions 带待确认动作',
                   len(done['actions']) == 1 and done['actions'][0]['status'] == 'awaiting_confirmation',
                   str(len(done['actions'])))
        check_true('动作带人话展示', bool(done['actions'][0]['display']['fields']), '')

    print()
    print('=== 3. 同步口径 run_turn：与流式同一实现 ===')
    fake3 = _FakeClient(rounds=[[_Chunk(_Delta(content='同步口径回复。'))]])
    async with SessionLocal() as session:
        agent_session = AgentSession(user_id=1, title=f'CHK{RUN}同步口径')
        session.add(agent_session)
        await session.commit()

        collected: list[tuple[str, dict]] = []
        with patch.object(runtime, '_client', lambda: fake3):
            result = await runtime.run_turn(
                session, agent_session=agent_session, user=user,
                text=f'CHK{RUN} 同步', on_event=lambda k, d: collected.append((k, d)),
            )
        check('同步返回 reply', result['reply'], '同步口径回复。')
        check_true('on_event 回调仍可用', any(k == 'done' for k, _ in collected), str([k for k, _ in collected]))

    print()
    print('=== 4. 模型未配置：notice + done，无 delta ===')
    async with SessionLocal() as session:
        agent_session = AgentSession(user_id=1, title=f'CHK{RUN}未配模型')
        session.add(agent_session)
        await session.commit()

        with patch.object(runtime, 'model_ready', lambda: False):
            events = await collect_events(session, agent_session, user, f'CHK{RUN} 未配模型')
        kinds = [k for k, _ in events]
        check_true('有 notice 无 delta', 'notice' in kinds and 'delta' not in kinds, str(kinds))
        check_true('done.reply 说明原因', 'DEEPSEEK_API_KEY' in dict(events)['done']['reply'], '')


if __name__ == '__main__':
    async def _driver():
        print('=== 清库（跑前）===')
        await clean(verbose=True)
        print()
        try:
            await main()
        finally:
            print()
            print('=== 清库（跑后）===')
            await clean(verbose=True)

    asyncio.run(_driver())
    print()
    if FAILURES:
        print(f'FAILED（{len(FAILURES)}）: {FAILURES}')
        sys.exit(1)
    print('全部通过')
