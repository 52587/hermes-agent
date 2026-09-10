"""Durable single-owner routing through actual Discord ingress and SessionDB."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from hermes_state import SessionDB
from plugins.platforms.discord import adapter as module


class Thread:
    def __init__(self, tid, owner_id):
        self.id, self.owner_id, self.parent_id = tid, owner_id, 100
        self.name, self.topic = 'task', None
        self.guild = SimpleNamespace(id=10, name='server')
        self.messages = []

    def history(self, **kwargs):
        async def iterate():
            for msg in self.messages:
                yield msg
        return iterate()


def message(thread, mid, text='continue', author=42, bot=False, mentions=()):
    return SimpleNamespace(id=mid, content=text, clean_content=text, channel=thread,
        guild=thread.guild, author=SimpleNamespace(id=author, bot=bot, display_name=str(author)),
        mentions=list(mentions), type=module.discord.MessageType.default, reference=None,
        attachments=[], created_at=datetime.now(timezone.utc), message_snapshots=[])


@pytest.fixture
def team(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    monkeypatch.setenv('DISCORD_ALLOW_BOTS', 'none')
    monkeypatch.setattr(module.discord, 'Thread', Thread)
    db = SessionDB(tmp_path / 'state.db')
    runner = SimpleNamespace(session_store=SimpleNamespace(_routing_db=db), adapters={}, _profile_adapters={},
                             _profile_name_for_source=lambda source: None)
    agents = []
    for bid, profile in [(111, 'default'), (222, 'engineer')]:
        a = module.DiscordAdapter(PlatformConfig(enabled=True, token='test', extra={
            'thread_owner_routing': True, 'require_mention': True, 'thread_require_mention': True,
            'history_backfill': True}))
        a._client = SimpleNamespace(user=SimpleNamespace(id=bid, bot=True))
        a._allowed_user_ids = {'42'}
        a.set_owner_profile(profile)
        a.gateway_runner = runner
        a._ready_event.set()
        a._text_batch_delay_seconds = 0
        a.handle_message = AsyncMock()
        runner._profile_adapters[profile] = {Platform.DISCORD: a}
        agents.append(a)
    yield agents, runner
    db.close()
    runner.session_store._routing_db.close()


@pytest.mark.asyncio
async def test_plain_followups_switch_and_restart_stay_in_thread(team, tmp_path):
    (a, b), runner = team
    first, second = Thread(1001, 111), Thread(1002, 222)
    assert await a._dispatch_discord_message(message(first, 10))
    assert not await b._dispatch_discord_message(message(first, 10))
    assert await b._dispatch_discord_message(message(second, 11))
    assert not await a._dispatch_discord_message(message(second, 11))
    # Raw mention switches even when Discord has not populated message.mentions.
    assert not await a._dispatch_discord_message(message(first, 20, '<@222> take over'))
    assert await b._dispatch_discord_message(message(first, 20, '<@222> take over'))
    runner.session_store._routing_db.close()
    runner.session_store._routing_db = SessionDB(tmp_path / 'state.db')
    for agent in (a, b):
        agent._threads.clear()  # Ownership must not depend on participation memory.
    assert not await a._dispatch_discord_message(message(first, 30))
    assert await b._dispatch_discord_message(message(first, 30))
    event = b.handle_message.await_args.args[0]
    assert event.source.thread_id == '1001' and event.source.chat_id == '1001'
    assert event.source._transport_adapter_ref() is b
    assert b._session_key_profile(event.source) == 'engineer'
    assert a._event_session_key(event) != b._event_session_key(event)
    assert await b._dispatch_discord_message(message(second, 31))
    # Bot reports cannot switch human conversation ownership.
    assert not await a._dispatch_discord_message(message(first, 40, '<@111>', author=222, bot=True))
    assert b._in_bot_thread(message(first, 41))


@pytest.mark.asyncio
async def test_recovery_switch_order_permissions_and_reply_pings(team):
    (a, b), runner = team
    thread = Thread(1001, 111)
    assert not await b._dispatch_discord_message(message(thread, 5, '<@222>', author=99))
    b.config.extra['ignored_channels'] = '100'
    assert not await b._dispatch_discord_message(message(thread, 6, '<@222> take over'))
    b.config.extra.pop('ignored_channels')
    assert a._in_bot_thread(message(thread, 7))
    assert await b._dispatch_recovered_message(message(thread, 20, '<@222> take over'))
    assert b._in_bot_thread(message(thread, 21))
    # Late replay may answer its explicit addressee, but cannot rewind the current owner.
    assert await a._dispatch_recovered_message(message(thread, 10, '<@111> older request'))
    assert b._in_bot_thread(message(thread, 22))
    ping = [a._client.user]
    assert await b._dispatch_discord_message(message(thread, 23, 'continue', mentions=ping))
    assert not await a._dispatch_discord_message(message(thread, 23, 'continue', mentions=ping))
    # A multi-mention chooses the first team member in the actual text, consistently.
    assert await a._dispatch_discord_message(message(thread, 24, '<@111> <@222> your view?'))
    assert not await b._dispatch_discord_message(message(thread, 24, '<@111> <@222> your view?'))


@pytest.mark.asyncio
async def test_handoff_context_includes_team_results_but_not_unrelated_bots(team):
    (a, b), _ = team
    thread = Thread(1001, 111)
    thread.messages = [message(thread, 3, 'third topic is astronomy', author=111, bot=True),
                       message(thread, 2, 'unrelated bot text', author=333, bot=True),
                       message(thread, 1, 'research three topics')]
    context = await b._fetch_channel_context(thread, message(thread, 4))
    assert 'third topic is astronomy' in context
    assert 'research three topics' in context
    assert 'unrelated bot text' not in context
    b.config.extra['thread_owner_routing'] = False
    assert 'third topic is astronomy' not in await b._fetch_channel_context(thread, message(thread, 5))


def test_config_opt_in_and_default_mention_policy(team):
    (a, _), _ = team
    assert module._apply_yaml_config({}, {'thread_owner_routing': True})['thread_owner_routing'] is True
    thread = Thread(1001, 111)
    a.config.extra['thread_owner_routing'] = False
    a._threads.mark('1001')
    assert not a._in_bot_thread(message(thread, 1))
    a.config.extra['thread_require_mention'] = False
    assert a._in_bot_thread(message(thread, 2))
