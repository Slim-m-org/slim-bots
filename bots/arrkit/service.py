"""One outside HTTP/JSON service: its url and key from the environment, and the async call the bots make."""

from slimbots.urlpolicy import service_url_problem
from slimbots.web import JsonService, ServiceAuthError

AuthError = ServiceAuthError


class Service:
    """A Sonarr-shaped api: `X-Api-Key` header, JSON in and out, over the library's `JsonService`."""

    def __init__(self, name, api_root):
        self.name = name
        self.api_root = api_root
        self.url = ""
        self.key = ""
        self.poll_seconds = 60
        self.allow_http = False
        self.transport = None

    def configure(self, bot):
        """Reads `<NAME>_URL`, `<NAME>_API_KEY`, `<NAME>_POLL_SECONDS` and `<NAME>_ALLOW_HTTP` through `bot.setting()`."""
        prefix = self.name.upper()
        self.url = (bot.setting(f"{prefix}_URL", required=True) or "").rstrip("/")
        self.key = bot.setting(f"{prefix}_API_KEY", required=True) or ""
        self.poll_seconds = bot.setting(f"{prefix}_POLL_SECONDS", 60, type=int)
        self.allow_http = bot.setting(f"{prefix}_ALLOW_HTTP", False, type=bool)

    def problem(self):
        """Plaintext is fine on loopback, to a bare docker service name on the host's bridge, or to a LAN host the operator opted into."""
        if not self.url:
            return None
        prefix = self.name.upper()
        return service_url_problem(
            f"{prefix}_URL", self.url, allow_http=self.allow_http, bare_hostnames=True, opt_in=f"{prefix}_ALLOW_HTTP", key_setting=f"{prefix}_API_KEY",
        )

    async def call(self, method, path, params=None, body=None):
        """One authenticated call returning parsed JSON; a rejected key raises `AuthError`."""
        service = JsonService(
            self.name, f"{self.url}{self.api_root}", headers={"x-api-key": self.key}, user_agent=f"slimm-bot-{self.name}/1.0",
            transport=self.transport,
        )
        return await service.request(method, path, params=params or None, body=body)
