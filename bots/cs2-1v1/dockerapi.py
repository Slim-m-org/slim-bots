"""A small Docker Engine API client that only ever reaches containers carrying this bot's label; see README.md.
It talks to a socket proxy over http, never to /var/run/docker.sock, and no error text carries an env value."""

import json
import struct

import httpx

OWNER_LABEL = "slimm.bot"
OWNER_VALUE = "cs2-1v1"
TIMEOUT_SECONDS = 30
PULL_TIMEOUT_SECONDS = 1800


class DockerError(Exception):
    """A Docker call that failed; `str(self)` is safe to show and never carries env values."""


class NotOwned(DockerError):
    """The container does not carry this bot's label, so it is never touched."""


class ImageMissing(DockerError):
    """The image to create from is not on the host yet."""


def owner_filter():
    return json.dumps({"label": [f"{OWNER_LABEL}={OWNER_VALUE}"]})


def is_owned(labels):
    return (labels or {}).get(OWNER_LABEL) == OWNER_VALUE


def split_image(image):
    """(repository, tag-or-digest) for the pull route."""
    if "@" in image:
        repo, digest = image.split("@", 1)
        return repo, digest
    repo, _, tag = image.rpartition(":")
    return (repo, tag) if repo and "/" not in tag else (image, "latest")


def demux_logs(raw):
    """Text of a non-tty log stream: 8-byte frame headers, then the payload."""
    out, at = [], 0
    while at + 8 <= len(raw):
        (size,) = struct.unpack(">I", raw[at + 4 : at + 8])
        out.append(raw[at + 8 : at + 8 + size])
        at += 8 + size
    return b"".join(out).decode("utf-8", "replace")


class DockerClient:
    """list, create, start, stop, inspect and logs for owned containers, plus an image pull."""

    def __init__(self, base_url, transport=None):
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=TIMEOUT_SECONDS, transport=transport)

    async def aclose(self):
        await self._http.aclose()

    async def _call(self, method, path, *, ok=(200, 201, 204), **kwargs):
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as err:
            raise DockerError(f"the Docker proxy could not be reached ({type(err).__name__})") from None
        if response.status_code not in ok:
            raise DockerError(f"Docker answered HTTP {response.status_code} for {method} {path.split('?')[0].rsplit('/', 1)[-1]}")
        return response

    async def list_owned(self):
        """Every container with this bot's label; the label is re-checked in case a proxy ignores the filter."""
        response = await self._call("GET", "/containers/json", params={"all": "true", "filters": owner_filter()})
        return [item for item in response.json() if is_owned(item.get("Labels"))]

    async def inspect_owned(self, container_id):
        """The container's details, None when it is gone, NotOwned when it lacks the label."""
        response = await self._call("GET", f"/containers/{container_id}/json", ok=(200, 404))
        if response.status_code == 404:
            return None
        data = response.json()
        if not is_owned(data.get("Config", {}).get("Labels")):
            raise NotOwned("that container is not one of mine")
        return data

    async def create(self, name, spec):
        """The new container's id; a missing image raises ImageMissing so the caller can pull and retry."""
        spec = {**spec, "Labels": {**spec.get("Labels", {}), OWNER_LABEL: OWNER_VALUE}}
        response = await self._call("POST", "/containers/create", params={"name": name}, json=spec, ok=(201, 404))
        if response.status_code == 404:
            raise ImageMissing("the server image is not on the host yet")
        return response.json()["Id"]

    async def start(self, container_id):
        if await self.inspect_owned(container_id) is None:
            raise DockerError("the container is gone")
        await self._call("POST", f"/containers/{container_id}/start", ok=(204, 304))

    async def stop(self, container_id):
        """Stops an owned container; one that is already gone is a success."""
        if await self.inspect_owned(container_id) is None:
            return
        await self._call("POST", f"/containers/{container_id}/stop", params={"t": "10"}, ok=(204, 304, 404))

    async def logs(self, container_id, tail=40):
        if await self.inspect_owned(container_id) is None:
            return ""
        response = await self._call("GET", f"/containers/{container_id}/logs", params={"stdout": "1", "stderr": "1", "tail": str(tail)})
        return demux_logs(response.content)

    async def pull(self, image):
        repo, tag = split_image(image)
        await self._call("POST", "/images/create", params={"fromImage": repo, "tag": tag}, timeout=PULL_TIMEOUT_SECONDS)
