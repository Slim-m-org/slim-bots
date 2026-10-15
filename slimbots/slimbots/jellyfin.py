"""The Jellyfin api's auth and url rules, shared by the bots that talk to it so none carries a copy."""

from __future__ import annotations

from typing import Any

from .urlpolicy import service_url_problem
from .web import JsonService, ServiceAuthError

JellyfinAuthError = ServiceAuthError


def auth_header(api_key: str) -> str:
    """`MediaBrowser Token="..."` - the one form this Jellyfin version accepts."""
    return f'MediaBrowser Token="{api_key}"'


def url_problem(url: str) -> str | None:
    """Why `JELLYFIN_URL` cannot carry the key, or None; plaintext is only for loopback."""
    return service_url_problem("JELLYFIN_URL", url) if url else None


def service(url: str, api_key: str, *, user_agent: str, **options: Any) -> JsonService:
    """A `JsonService` for the server at `url`; Jellyfin answers 401 for a bad key, never 403."""
    return JsonService(
        "jellyfin", url, headers={"authorization": auth_header(api_key)}, user_agent=user_agent,
        auth_statuses=(401,), **options,
    )
