"""The poll wiring: fatal on a rejected key or revoked token, otherwise retry next cycle."""

import asyncio
import sys

from slimbots import ApiError, style
from slimbots.http import is_token_revoked

from . import history, store
from .service import AuthError


async def send_history_post(bot, post):
    assert bot.client is not None, "send_history_post runs only once connected"
    text = history.render_text(post)
    embed = style.card(history.STYLES[post["kind"]], title=text)
    await bot.client.send(bot.channel, "", message_id=post["message_id"], **style.send_kwargs(embed, text))


async def history_poll_once(bot, core):
    """Announces new Sonarr/Radarr history; `core` supplies `api`, `fetch_history_since`, `plan_events`, `build_posts`."""
    cursor = await bot.store.run(store.get_cursor)
    if cursor is None:
        await core.bootstrap_cursor(bot.store)
        return
    records = await core.fetch_history_since(cursor)
    if not records:
        return
    events = await bot.store.run(core.plan_events, records)
    for post in core.build_posts(events):
        try:
            await send_history_post(bot, post)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            return
        await bot.store.run(store.mark_announced, post["marks"])
    await bot.store.run(store.advance_cursor, records[-1]["id"])


def install(bot, poll_once, service):
    """Polls the service every `poll_seconds`; a rejected key is fatal, anything else retries next cycle."""
    bot.every(lambda: service.poll_seconds, name=f"{service.name}-poll", fatal=(AuthError,))(poll_once)


def main(bot, service, extra_problem=None):
    """The entry point every bot here shares: refuse an unsafe url or setting, then run until told to stop."""
    bot.run_cli(check=[service.problem, extra_problem] if extra_problem else service.problem)
