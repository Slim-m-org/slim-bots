"""A fake Docker client and a scripted A2S answer for the bot's tests; the fake enforces the same label rule as the real client."""

from dockerapi import DockerError, ImageMissing, NotOwned, OWNER_LABEL, OWNER_VALUE, is_owned


class FakeDocker:
    def __init__(self):
        self.containers = {}
        self.calls = []
        self.images = set()
        self.down = False
        self._n = 0
        self.logs_text = ""

    def add_foreign(self, name="someone-elses"):
        self.containers[name] = {"Id": name, "Name": f"/{name}", "State": "running", "Labels": {}, "Env": []}

    def _check(self):
        if self.down:
            raise DockerError("the Docker proxy could not be reached (ConnectError)")

    async def list_owned(self):
        self._check()
        return [dict(c) for c in self.containers.values() if is_owned(c["Labels"])]

    async def inspect_owned(self, container_id):
        self._check()
        found = self.containers.get(container_id)
        if found is None:
            return None
        if not is_owned(found["Labels"]):
            raise NotOwned("that container is not one of mine")
        return {"Id": found["Id"], "Name": found["Name"], "Config": {"Labels": found["Labels"], "Env": found["Env"]}}

    async def create(self, name, spec):
        self._check()
        self.calls.append(("create", name, spec))
        if spec["Image"] not in self.images:
            raise ImageMissing("missing")
        self._n += 1
        container_id = f"c{self._n}"
        labels = {**spec["Labels"], OWNER_LABEL: OWNER_VALUE}
        self.containers[container_id] = {"Id": container_id, "Name": f"/{name}", "State": "created", "Labels": labels, "Env": spec["Env"]}
        return container_id

    async def start(self, container_id):
        self._check()
        await self.inspect_owned(container_id)
        self.calls.append(("start", container_id))
        self.containers[container_id]["State"] = "running"

    async def stop(self, container_id):
        self._check()
        if await self.inspect_owned(container_id) is None:
            return
        self.calls.append(("stop", container_id))
        del self.containers[container_id]

    async def logs(self, container_id, tail=40):
        self._check()
        return self.logs_text

    async def pull(self, image):
        self._check()
        self.calls.append(("pull", image))
        self.images.add(image)


def info(players=0, bots=0, game_map="de_dust2"):
    return {"map": game_map, "players": players, "max_players": 10, "bots": bots}
