"""A discord.py-shaped bot framework for slim-m; see docs/framework.md."""

from . import catchup, cursor, style
from . import events
from . import migrations
from .bot import Bot
from .canvas import Canvas
from .chooser import Chooser, Pick
from .client import socket_url
from .commands import Command, Group
from .components import Button, rows
from .context import Context
from .converters import Duration, TimeOfDay
from .embeds import Embed
from .exceptions import (
    BadArgument,
    CheckFailure,
    CommandError,
    CommandNotFound,
    CommandOnCooldown,
    MissingPermissions,
    MissingRequiredArgument,
    SlimBotsError,
)
from .http import ApiError
from .http import AsyncClient
from .interactions import Interaction
from .limits import Cooldown, Quota, RateLimiter, ValidationError, require_int, require_len, require_range
from .models import Attachment, Channel, DmConversation, Member, Message, Role
from .permissions import Permissions
from .space import Space
from .store import KeyValue, Ledger, Store
from .tables import KeyValueTable, LedgerTable
from .voice import Voice, VoiceError, VoiceSession
from .web import JsonService, ServiceAuthError, ServiceError, ServiceHTTPError, ServiceTooLarge, ServiceUnreachable

__all__ = [
    "ApiError",
    "AsyncClient",
    "Attachment",
    "BadArgument",
    "Bot",
    "Button",
    "Canvas",
    "Channel",
    "CheckFailure",
    "Chooser",
    "Command",
    "CommandError",
    "CommandNotFound",
    "CommandOnCooldown",
    "Context",
    "Cooldown",
    "DmConversation",
    "Duration",
    "Embed",
    "Group",
    "Interaction",
    "KeyValue",
    "KeyValueTable",
    "Ledger",
    "LedgerTable",
    "JsonService",
    "Member",
    "Message",
    "MissingPermissions",
    "MissingRequiredArgument",
    "Permissions",
    "Pick",
    "Quota",
    "RateLimiter",
    "Role",
    "ServiceAuthError",
    "ServiceError",
    "ServiceHTTPError",
    "ServiceTooLarge",
    "ServiceUnreachable",
    "SlimBotsError",
    "Space",
    "Store",
    "TimeOfDay",
    "ValidationError",
    "Voice",
    "VoiceError",
    "VoiceSession",
    "catchup",
    "cursor",
    "events",
    "migrations",
    "require_int",
    "require_len",
    "require_range",
    "rows",
    "socket_url",
]
