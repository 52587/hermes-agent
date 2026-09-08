"""Discord run notifications backed by the existing gateway session index."""

import asyncio
import hashlib

from gateway.config import Platform
from gateway.session import SessionSource, SessionStore
from hermes_constants import get_hermes_home
from hermes_time import now


def _find_run_thread(store, run_id, parent_id):
    for entry in store.list_sessions():
        binding = entry.metadata.get("automation_run", {})
        if binding.get("run_id") == run_id and binding.get("parent_chat_id") == parent_id:
            return entry
    return None


def _bind_run_thread(store, job, thread_id, parent_id, content):
    """Snapshot this run, never another run or the parent channel's history."""
    source = SessionSource(
        platform=Platform.DISCORD, chat_id=thread_id, thread_id=thread_id,
        chat_type="thread", chat_name=job.get("name"),
        user_id=(job.get("origin") or {}).get("user_id"),
        automation_thread=True,
    )
    entry = store.get_or_create_session(source)
    if not entry.metadata.get("automation_run"):
        db = store._db
        if db is None:
            raise RuntimeError("Discord automation threads require the session database")
        session_id = job.get("_cron_session_id")
        messages = []
        if session_id and db.get_session(session_id):
            tip = db.get_compression_tip(session_id) or session_id
            messages = db.get_messages_as_conversation(tip)
        if not messages:
            messages = [{"role": "user", "content": job.get("prompt") or job.get("name") or job["id"]}]
        if not messages or messages[-1].get("content") != content:
            messages.append({"role": "assistant", "content": content})
        # The thread is not open yet, so this is initialization of a fresh
        # conversation, never a rewrite of a user's ongoing conversation.
        db.replace_messages(entry.session_id, messages)
        store.set_session_metadata(entry.session_key, "automation_run", {
            "automation_id": job["id"], "run_id": job["execution_id"],
            "thread_id": thread_id, "parent_chat_id": parent_id,
            "source_session_id": session_id,
            "notification_hash": hashlib.sha256(content.encode()).hexdigest(),
        })
    return entry


def _record_run_update(store, entry, content):
    binding = entry.metadata["automation_run"]
    digest = hashlib.sha256(content.encode()).hexdigest()
    if digest != binding.get("notification_hash"):
        session_id = store._db.get_compression_tip(entry.session_id) or entry.session_id
        store._db.append_message(session_id, role="user", content=f"[Automation update]\n{content}")
        store.set_session_metadata(entry.session_key, "automation_run", {
            **binding, "notification_hash": digest,
        })


async def prepare_run_thread(job, config, pconfig, chat_id, content, adapter=None):
    """Post a summary, persist its run context, then open its public thread.

    Discord uses the starter message's ID as the thread ID, letting us save
    the routing entry before replies can arrive. A retry reuses that entry.
    DMs keep their existing delivery because Discord cannot thread them.
    """
    import aiohttp
    from gateway.platforms.base import resolve_proxy_url, proxy_kwargs_for_aiohttp

    store = getattr(adapter, "_session_store", None)
    owns_store = store is None
    if store is None:
        store = await asyncio.to_thread(SessionStore, get_hermes_home() / "sessions", config)
    if store._db is None:
        raise RuntimeError("Discord automation threads require the session database")

    try:
        # The live client's HTTP implementation already handles Discord rate
        # limits. Standalone cron uses the same configured Bot token and proxy.
        client = getattr(adapter, "_client", None)
        token = pconfig.token
        if not client and not token:
            from agent.secret_scope import get_secret
            token = get_secret("DISCORD_BOT_TOKEN", "")
        if not client and not token:
            raise RuntimeError("DISCORD_BOT_TOKEN is not configured")
        session_kwargs, request_kwargs = proxy_kwargs_for_aiohttp(
            resolve_proxy_url(platform_env_var="DISCORD_PROXY")
        )
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30), **session_kwargs,
        ) as http:
            async def request(method, path, payload=None):
                if client:
                    from discord.http import Route
                    return await client.http.request(Route(method, path), json=payload)
                async with http.request(
                    method, f"https://discord.com/api/v10{path}",
                    headers={"Authorization": f"Bot {token}"},
                    json=payload, **request_kwargs,
                ) as response:
                    if response.status not in (200, 201):
                        raise RuntimeError(f"Discord thread API returned HTTP {response.status}")
                    return await response.json()

            channel = await request("GET", f"/channels/{chat_id}")
            if channel["type"] in (1, 3):
                return None
            if channel["type"] in (10, 11, 12):
                chat_id = str(channel["parent_id"])
                channel = await request("GET", f"/channels/{chat_id}")
            if channel["type"] not in (0, 5):
                raise RuntimeError("Discord automation destination must be a text or announcement channel")
            chat_id = str(chat_id)
            entry = await asyncio.to_thread(_find_run_thread, store, job["execution_id"], chat_id)
            name = " ".join(str(job.get("name") or job["id"]).split())[:75]
            thread_name = f"{name} · {now().strftime('%b %d')}"
            if entry is None:
                summary = f"{name} — {job.get('_delivery_status', 'Completed')}\nDetails and follow-up in the thread."
                nonce = hashlib.sha256(f"{job['execution_id']}:{chat_id}".encode()).hexdigest()[:24]
                seed = await request("POST", f"/channels/{chat_id}/messages", {
                    "content": summary, "allowed_mentions": {"parse": []},
                    "nonce": nonce, "enforce_nonce": True,
                })
                entry = await asyncio.to_thread(
                    _bind_run_thread, store, job, str(seed["id"]), chat_id, content,
                )
            binding = entry.metadata["automation_run"]
            thread_id = binding["thread_id"]
            if not binding.get("thread_created"):
                # A crash after the REST call but before the metadata write is
                # recoverable: GET the starter message to discover its thread.
                seed = await request("GET", f"/channels/{chat_id}/messages/{thread_id}")
                if not seed.get("thread"):
                    await request("POST", f"/channels/{chat_id}/messages/{thread_id}/threads", {
                        "name": thread_name, "auto_archive_duration": 1440,
                    })
                await asyncio.to_thread(store.set_session_metadata, entry.session_key, "automation_run", {
                    **binding, "thread_created": True,
                })
            if adapter is not None:
                await asyncio.to_thread(adapter._threads.mark, thread_id)
            await asyncio.to_thread(_record_run_update, store, entry, content)
            return thread_id
    finally:
        if owns_store:
            await asyncio.to_thread(store._db.close)
