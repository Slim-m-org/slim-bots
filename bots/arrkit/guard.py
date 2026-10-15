"""What every command of these bots does around a service call: a cooldown, and a sentence when it fails."""

from slimbots.limits import Cooldown
from slimbots.web import ServiceError

from .service import AuthError

COMMAND_COOLDOWN_SECONDS = 10
UNREACHABLE = (ServiceError, TimeoutError, OSError)


class Guard:
    """One per bot: its per-member cooldown and its service's failure wording."""

    def __init__(self, service):
        self.service = service
        self.cooldown = Cooldown(COMMAND_COOLDOWN_SECONDS)

    async def throttled(self, ctx):
        wait = self.cooldown.check(ctx.author.id)
        if wait:
            await ctx.reply(wait)
        return bool(wait)

    async def call(self, ctx, fn, *args):
        """Awaits `fn(*args)`; on failure answers with a sentence and returns None."""
        try:
            return await fn(*args)
        except AuthError:
            await ctx.reply(f"{self.service.name} rejected the bot's api key - an admin needs to fix it.")
        except UNREACHABLE:
            await ctx.reply(f"{self.service.name} is unavailable right now (it did not answer).")
        return None
