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
        self.transport = None

    def configure(self, bot):
        """Reads `<NAME>_URL`, `<NAME>_API_KEY` and `<NAME>_POLL_SECONDS` through `bot.setting()`."""
        prefix = self.name.upper()
        self.url = (bot.setting(f"{prefix}_URL", required=True) or "").rstrip("/")
        self.key = bot.setting(f"{prefix}_API_KEY", required=True) or ""
        self.poll_seconds = bot.setting(f"{prefix}_POLL_SECONDS", 60, type=int)

    def problem(self):
        """Plaintext is fine on loopback or to a bare docker service name, which never leaves the host's bridge."""
        if not self.url:
            return None
        return service_url_problem(f"{self.name.upper()}_URL", self.url, bare_hostnames=True)

    async def call(self, method, path, params=None, body=None):
        """One authenticated call returning parsed JSON; a rejected key raises `AuthError`."""
        service = JsonService(
            self.name, f"{self.url}{self.api_root}", headers={"x-api-key": self.key}, user_agent=f"slimm-bot-{self.name}/1.0",
            transport=self.transport,
        )
        return await service.request(method, path, params=params or None, body=body)
