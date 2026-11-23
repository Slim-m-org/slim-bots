"""A connector's settings, read once from `GET /connectors/me` with its own token; `PELICAN_URL` reads `pelican_url`.
Secrets arrive in plain text over that authenticated call and are never logged here."""

from typing import Any

import httpx

from .client import base_url_problem

SELF_PATH = "/connectors/me"
TIMEOUT_SECONDS = 15

_REFUSALS = {
    401: "the connector token was refused; make a new one in the Dock",
    403: "this token is not a connector's own token",
    409: "an admin has turned this connector off in the Dock",
}


class ConnectorError(Exception):
    """Why the host's settings could not be read; never carries the token or a secret."""


def fetch_settings(base: str, token: str, *, transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """The connector's settings by key, secrets included; raises `ConnectorError` with a reason to show."""
    if problem := base_url_problem(base):
        raise ConnectorError(problem)
    try:
        with httpx.Client(base_url=base, transport=transport, timeout=TIMEOUT_SECONDS) as http:
            response = http.get(SELF_PATH, headers={"authorization": f"Bearer {token}"})
    except httpx.HTTPError as err:
        raise ConnectorError(f"could not reach {base}: {type(err).__name__}") from None
    if response.status_code in _REFUSALS:
        raise ConnectorError(_REFUSALS[response.status_code])
    if response.status_code != 200:
        raise ConnectorError(f"reading the connector's settings answered {response.status_code}")
    settings = response.json().get("settings")
    if not isinstance(settings, dict):
        raise ConnectorError("the host's answer had no settings")
    return settings


def setting_key(name: str) -> str:
    """The manifest key an environment-style name reads: `PELICAN_API_KEY` is `pelican_api_key`."""
    return name.lower()


def from_host(value: Any, kind: type) -> Any:
    """A JSON setting value converted the way `Bot.setting` converts the environment's text."""
    if kind is bool:
        return value if isinstance(value, bool) else str(value).strip().lower() in {"1", "true", "yes", "on"}
    if kind is int:
        return int(value)
    if kind is float:
        return float(value)
    if kind is list:
        return value if isinstance(value, list) else [part.strip() for part in str(value).split(",") if part.strip()]
    return value if isinstance(value, str) else str(value)
