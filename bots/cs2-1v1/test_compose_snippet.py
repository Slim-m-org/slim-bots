#!/usr/bin/env python3
"""The network layout in compose.snippet.yml: game servers must never share a network with the Docker socket proxy; run: python3 test_compose_snippet.py."""

import os
import re

from slimbots.testing import run_module_tests

SNIPPET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compose.snippet.yml")


def _lines():
    """(indent, text) for each line with its comment and trailing space removed, blank lines dropped."""
    with open(SNIPPET, encoding="utf-8") as handle:
        for raw in handle:
            text = raw.split(" #", 1)[0].rstrip() if not raw.lstrip().startswith("#") else ""
            if text:
                yield len(text) - len(text.lstrip()), text.strip()


def _flow_list(value):
    match = re.fullmatch(r"\[(.*)\]", value)
    assert match, f"expected an inline list, got {value!r}"
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def snippet_shape():
    """The networks each service joins, each network's keys, and the cs2-1v1 bot's environment."""
    services, networks, environment = {}, {}, {}
    section = service = network = None
    in_env = False
    for indent, text in _lines():
        key, _, value = text.partition(":")
        value = value.strip()
        if indent == 0:
            section, service, network = key, None, None
        elif indent == 2 and section == "services":
            service, in_env = key, False
            services[service] = []
        elif indent == 2 and section == "networks":
            network = key
            networks[network] = {}
        elif indent == 4 and section == "services" and key == "networks":
            services[service] = _flow_list(value)
        elif indent == 4 and section == "services":
            in_env = key == "environment" and service == "cs2-1v1"
        elif indent == 6 and in_env:
            environment[key] = value
        elif indent == 4 and section == "networks":
            networks[network][key] = value
    return services, networks, environment


def test_the_socket_proxy_sits_on_an_internal_network_only_the_bot_shares():
    services, networks, _ = snippet_shape()
    proxy_networks = services["cs2-docker-proxy"]
    assert proxy_networks, "the proxy must name its networks"
    for name in proxy_networks:
        assert networks.get(name, {}).get("internal") == "true", f"the proxy's network {name} must be internal: true"
        sharing = sorted(s for s, joined in services.items() if name in joined)
        assert sharing == ["cs2-1v1", "cs2-docker-proxy"], f"{name} is shared by {sharing}"


def test_the_game_server_network_is_not_one_the_proxy_joins():
    services, networks, environment = snippet_shape()
    game_network = environment["CS2_DOCKER_NETWORK"]
    game_keys = [name for name, keys in networks.items() if keys.get("name") == game_network or name == game_network]
    assert game_keys, f"CS2_DOCKER_NETWORK {game_network} is not declared in the snippet"
    assert not set(game_keys) & set(services["cs2-docker-proxy"]), "game servers join a network the socket proxy listens on"
    assert set(game_keys) <= set(services["cs2-1v1"]), "the bot must join the game server network to query servers"


def test_the_bot_reaches_the_proxy_by_a_name_on_their_shared_network():
    services, _, environment = snippet_shape()
    assert environment["CS2_DOCKER_HOST"] == "http://cs2-docker-proxy:2375"
    assert set(services["cs2-docker-proxy"]) <= set(services["cs2-1v1"])


if __name__ == "__main__":
    run_module_tests(globals())
