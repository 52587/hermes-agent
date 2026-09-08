"""Run delivery and inbound routing with real SQLite; only Discord is faked."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from cron.discord_threads import prepare_run_thread
from cron.scheduler import _deliver_result
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import SendResult
from gateway.session import SessionSource, SessionStore, build_session_key
from plugins.platforms.discord.adapter import DiscordAdapter


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pconfig = PlatformConfig(enabled=True, token="test-token")
    config = GatewayConfig(platforms={Platform.DISCORD: pconfig})
    store = SessionStore(tmp_path / "sessions", config)
    adapter = DiscordAdapter(pconfig)
    adapter._session_store = store
    calls, seeds, opened = [], {}, set()

    async def request(route, *, json=None):
        path = route.path
        calls.append((route.method, path, json))
        if path == "/channels/100":
            return {"id": "100", "type": 0}
        if path == "/channels/100/messages":
            seed_id = str(200 + len(seeds))
            seeds[seed_id] = json
            return {"id": seed_id}
        seed_id = path.split("/")[4]
        if path.endswith("/threads"):
            # The mapping and full context must already be durable when
            # Discord makes the thread visible to participants.
            fresh = SessionStore(tmp_path / "sessions", config)
            entry = fresh.get_automation_thread(seed_id)
            assert entry and fresh.load_transcript(entry.session_id)
            opened.add(seed_id)
            return {"id": seed_id}
        return {"id": seed_id, **({"thread": {"id": seed_id}} if seed_id in opened else {})}

    adapter._client = SimpleNamespace(http=SimpleNamespace(request=request), user=SimpleNamespace(id=999))
    adapter.send = AsyncMock(return_value=SendResult(success=True, message_id="900"))
    monkeypatch.setattr("gateway.config.load_gateway_config", lambda: config)
    monkeypatch.setattr("cron.scheduler.load_config", lambda: {})
    return config, pconfig, store, adapter, calls


@pytest.mark.asyncio
async def test_scheduler_delivers_each_run_to_its_own_durable_context(setup, tmp_path):
    config, _, store, adapter, calls = setup
    for run_id, name, detail in [("run-a", "YouTube Research", "Third topic: whales"), ("run-b", "GitHub Task", "Fix parser")]:
        store._db.create_session(run_id, source="cron")
        store._db.replace_messages(run_id, [
            {"role": "user", "content": f"Research {name}"},
            {"role": "assistant", "content": detail},
        ])
        error = await asyncio.to_thread(_deliver_result, {
            "id": name, "name": name, "execution_id": run_id,
            "_cron_session_id": run_id, "deliver": "discord:100",
        }, detail, {Platform.DISCORD: adapter}, asyncio.get_running_loop())
        assert error is None
    assert len([call for call in calls if call[1] == "/channels/100/messages"]) == 2
    destinations = [call.kwargs["metadata"]["thread_id"] for call in adapter.send.await_args_list]
    assert destinations == ["200", "201"]
    # Simulate a restart and an idle interval long enough to trigger normal
    # session resets. The thread route must retain its conversation anyway.
    restarted = SessionStore(tmp_path / "sessions", config)
    entries = [restarted.get_automation_thread(t) for t in destinations]
    assert entries[0].session_id != entries[1].session_id
    for thread_id, entry in zip(destinations, entries):
        entry.updated_at = datetime.now(timezone.utc) - timedelta(days=30)
        source = SessionSource(platform=Platform.DISCORD, chat_id=thread_id,
            thread_id=thread_id, chat_type="thread", user_id="42", automation_thread=True)
        assert restarted.get_or_create_session(source).session_id == entry.session_id
        assert not restarted._is_session_expired(entry)
        assert not restarted.is_session_finalizable(entry)
    assert "whales" in str(restarted.load_transcript(entries[0].session_id))
    assert "Fix parser" not in str(restarted.load_transcript(entries[0].session_id))
    assert entries[0].metadata["automation_run"]["run_id"] == "run-a"
    # Normal agent teardown also ends a DB session. Recovery must retain
    # the run binding instead of rebuilding an unlabelled expiring entry.
    for entry in entries:
        restarted._db.end_session(entry.session_id, "agent_close")
    again = SessionStore(tmp_path / "sessions", config)
    assert again.get_automation_thread("200").session_id == entries[0].session_id
    assert again.get_automation_thread("201").session_id == entries[1].session_id
    again._db.end_session(entries[0].session_id, "compression")
    again._db.create_session("compressed", source="discord", parent_session_id=entries[0].session_id)
    compressed = SessionStore(tmp_path / "sessions", config)
    assert compressed.get_automation_thread("200").session_id == "compressed"
    assert compressed.get_automation_thread("200").metadata["automation_run"]["run_id"] == "run-a"


@pytest.mark.asyncio
async def test_retry_reuses_thread_and_failure_never_sends_details_to_parent(setup):
    config, pconfig, store, adapter, calls = setup
    job = {"id": "job", "execution_id": "run", "name": "Research", "prompt": "Research whales"}
    first = await prepare_run_thread(job, config, pconfig, "100", "Whales", adapter)
    assert await prepare_run_thread(job, config, pconfig, "100", "Whales", adapter) == first
    assert len([call for call in calls if call[1] == "/channels/100/messages"]) == 1
    assert len(store.list_sessions()) == 1
    assert await prepare_run_thread(job, config, pconfig, "100", "More whale research", adapter) == first
    assert "More whale research" in str(store.load_transcript(store.get_automation_thread(first).session_id))
    adapter._client.http.request = AsyncMock(side_effect=RuntimeError("Missing thread permission"))
    error = await asyncio.to_thread(_deliver_result, dict(job, execution_id="failed-run", deliver="discord:100"),
        "Private details", {Platform.DISCORD: adapter}, asyncio.get_running_loop())
    assert "Missing thread permission" in error
    adapter.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_reply_without_mention_resolves_run_after_restart(setup, tmp_path, monkeypatch):
    config, pconfig, _, adapter, _ = setup
    config.thread_sessions_per_user = True
    thread_id = await prepare_run_thread(
        {"id": "youtube", "execution_id": "run", "name": "YouTube", "prompt": "Find topics"},
        config, pconfig, "100", "Third topic: whales", adapter,
    )
    restarted = DiscordAdapter(pconfig)
    restarted._session_store = SessionStore(tmp_path / "sessions", config)
    restarted._client = SimpleNamespace(user=SimpleNamespace(id=999))
    restarted._threads.clear()  # also exercises tracker eviction / missing cache
    restarted._text_batch_delay_seconds = 0
    restarted.handle_message = AsyncMock()
    monkeypatch.setenv("DISCORD_REQUIRE_MENTION", "true")
    monkeypatch.setenv("DISCORD_THREAD_REQUIRE_MENTION", "true")

    class Thread:
        id = int(thread_id)
        name = "YouTube"
        parent_id = 100
        parent = SimpleNamespace(id=100, name="automation", topic=None)
        guild = SimpleNamespace(id=1, name="Home")
        def history(self, **kwargs):
            async def empty():
                for item in []:
                    yield item
            return empty()

    monkeypatch.setattr("plugins.platforms.discord.adapter.discord.Thread", Thread)
    message = SimpleNamespace(id=555, channel=Thread(), content="第三个选题继续研究",
        author=SimpleNamespace(id=42, display_name="User", name="User", bot=False),
        attachments=[], mentions=[], reference=None, created_at=datetime.now(timezone.utc), guild=Thread.guild)
    assert await restarted._handle_message(message)
    event = restarted.handle_message.await_args.args[0]
    entry = restarted._session_store.get_automation_thread(thread_id)
    assert event.source.chat_id == event.source.thread_id == thread_id
    assert build_session_key(event.source, thread_sessions_per_user=True) == entry.session_key
    assert restarted._session_store.get_or_create_session(event.source).session_id == entry.session_id
    assert "whales" in str(restarted._session_store.load_transcript(entry.session_id))


@pytest.mark.asyncio
async def test_standalone_uses_existing_token_and_publishes_resumable_route(setup, monkeypatch):
    config, pconfig, store, adapter, _ = setup
    store.list_sessions()  # live gateway has already loaded its routing cache
    handle = adapter._client.http.request

    class Response:
        status = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def json(self):
            return self.data

    class HTTP:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def request(self, method, url, *, headers, json, **kwargs):
            assert headers["Authorization"] == "Bot test-token"
            response = Response()
            async def enter():
                response.data = await handle(SimpleNamespace(method=method, path=url.split("/v10")[1]), json=json)
                return response
            class Pending(Response):
                async def __aenter__(self):
                    return await enter()
            return Pending()

    monkeypatch.setattr("aiohttp.ClientSession", HTTP)
    thread_id = await prepare_run_thread(
        {"id": "script", "execution_id": "script-run", "prompt": "Run report"},
        config, pconfig, "100", "Script report", None,
    )
    entry = store.get_automation_thread(thread_id)
    assert entry.metadata["automation_run"]["run_id"] == "script-run"
    assert "Script report" in str(store.load_transcript(entry.session_id))


@pytest.mark.asyncio
async def test_multiplex_bots_only_resume_their_own_run_context(setup, tmp_path, monkeypatch):
    from gateway.run import _profile_runtime_scope

    config, pconfig, store, adapter, _ = setup
    config.multiplex_profiles = True
    monkeypatch.setattr(Path, "home", lambda: tmp_path.parent)
    monkeypatch.setattr("hermes_cli.profiles._get_default_hermes_home", lambda: tmp_path)
    monkeypatch.setattr("hermes_cli.profiles._get_profiles_root", lambda: tmp_path / "profiles")
    routes = {}
    for profile, detail in [("silverwolf", "Third issue: parser"), ("herta", "Third topic: whales")]:
        home = tmp_path / "profiles" / profile
        home.mkdir(parents=True)
        (home / ".env").write_text("")
        with _profile_runtime_scope(home):
            adapter.set_owner_profile(profile)
            store._db.create_session(profile, source="cron")
            store._db.replace_messages(profile, [
                {"role": "user", "content": "Keep this run's private working notes"},
                {"role": "assistant", "content": detail},
            ])
            thread_id = await prepare_run_thread(
                {"id": profile, "execution_id": profile, "_cron_session_id": profile},
                config, pconfig, "100", "Completed", adapter,
            )
            entry = store.get_automation_thread(thread_id)
            routes[profile] = (thread_id, entry.session_id)

    # Each bot uses the same gateway SessionStore, but its own profile DB and
    # routing namespace. Reconstruct the store to exercise restart recovery.
    restarted = SessionStore(tmp_path / "sessions", config)
    for profile, (thread_id, conversation_id) in routes.items():
        with _profile_runtime_scope(tmp_path / "profiles" / profile):
            adapter.set_owner_profile(profile)
            adapter.set_session_store(restarted)
            assert await adapter._is_automation_thread(thread_id)
            other_thread = next(t for p, (t, _) in routes.items() if p != profile)
            assert not await adapter._is_automation_thread(other_thread)
            source = SessionSource(platform=Platform.DISCORD, chat_id=thread_id,
                thread_id=thread_id, chat_type="thread", profile=profile,
                user_id="42", automation_thread=True)
            entry = restarted.get_or_create_session(source)
            assert entry.session_id == conversation_id
            transcript = str(restarted.load_transcript(entry.session_id))
            assert ("parser" in transcript) == (profile == "silverwolf")
            assert ("whales" in transcript) == (profile == "herta")
