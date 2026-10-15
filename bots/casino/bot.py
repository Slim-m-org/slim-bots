#!/usr/bin/env python3
"""bot-casino: chip balance, coinflip, and blackjack (hit/stand/double/split/surrender); see README.md.
Split per docs/framework.md: this is the entry point, casino_core.py holds the primitives, economy.py/blackjack_cog.py are extensions."""

import secrets  # noqa: F401 - re-exported so test_bot.py can monkeypatch secrets.choice, as casino_core does
import time

from slimbots import Bot, RateLimiter

import blackjack  # noqa: F401 - re-exported so test_bot.py can reach casino.blackjack directly
import casino_core
from casino_core import (  # noqa: F401 - re-exported for test_bot.py/test_concurrency.py, which import these by name off `bot`
    MAX_AMOUNT, begin, credit, get_balance, init_db, resolve_amount, try_consume_request, try_debit,
)

COMMANDS_PER_WINDOW = 12
COMMAND_WINDOW_SECONDS = 10

bot = Bot(prefix="!", require_channels=True, default_data_path="casino.db", store_migrate=init_db)
_command_limiter = RateLimiter(COMMANDS_PER_WINDOW, COMMAND_WINDOW_SECONDS)


@bot.check
async def rate_limit(ctx):
    """A burst allowance against a script, not a play-speed cap on a person; see README.md."""
    return _command_limiter.check(ctx.author.id)


bot.load_extension("economy")
bot.load_extension("blackjack_cog")


@bot.every(lambda: casino_core.PRUNE_INTERVAL_SECONDS, name="casino-maintenance", delay_first=True, on="ready")
async def prune_processed():
    """Prunes processed_requests hourly; the row only needs to survive one reconnect gap."""
    await bot.store.run(casino_core.prune_processed_requests, int(time.time()) - casino_core.PROCESSED_REQUEST_RETENTION_SECONDS)


if __name__ == "__main__":
    bot.run_cli()
