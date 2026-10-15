"""The one URL policy: which service URLs may carry a key, and which destinations a bot may be pointed at."""

import pytest

from slimbots import urlpolicy
from slimbots.urlpolicy import PRIVATE, UNRESOLVABLE, destination_problem, is_loopback_host, is_public_address, service_url_problem


@pytest.mark.parametrize("address", [
    "10.0.0.5", "172.16.0.1", "172.31.255.255", "192.168.1.1", "127.0.0.1", "127.8.8.8", "169.254.169.254", "100.64.0.1",
    "100.127.255.254", "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255", "192.0.0.1", "198.18.0.1", "192.0.2.1",
    "::1", "::", "fe80::1", "fe80::1%eth0", "fc00::1", "fd12:3456::1", "ff02::1", "::ffff:10.0.0.1", "::ffff:127.0.0.1",
    "::ffff:169.254.169.254", "not-an-address", "", "2002:0a00:0001::1", "2002:c0a8:0101::", "64:ff9b::a00:1", "64:ff9b::7f00:1",
    "2001:0:4136:e378:8000:63bf:f5ff:fffe",
])
def test_a_non_public_address_is_refused(address):
    assert not is_public_address(address)


@pytest.mark.parametrize("address", [
    "8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8",
])
def test_a_public_address_is_allowed(address):
    assert is_public_address(address)


def resolver_for(*addresses):
    async def resolve(host):
        return list(addresses)

    return resolve


@pytest.mark.parametrize("answers, expected", [
    (["93.184.216.34"], None),
    (["93.184.216.34", "2606:4700:4700::1111"], None),
    (["10.0.0.5"], PRIVATE),
    (["93.184.216.34", "10.0.0.5"], PRIVATE),
    (["100.64.0.9"], PRIVATE),
    (["::ffff:192.168.0.1"], PRIVATE),
    (["fd00::1"], PRIVATE),
    ([], UNRESOLVABLE),
])
async def test_every_dns_answer_must_be_public(answers, expected):
    assert await destination_problem("https://feed.example/x", resolver=resolver_for(*answers)) == expected


async def test_a_name_that_does_not_resolve_is_unresolvable():
    async def broken(host):
        raise OSError("no such host")

    assert await destination_problem("https://nope.invalid/", resolver=broken) == UNRESOLVABLE


@pytest.mark.parametrize("url", ["ftp://example.com/x", "file:///etc/passwd", "//example.com/x", "https://", "example.com", ""])
async def test_only_http_and_https_with_a_host_are_destinations(url):
    assert await destination_problem(url, resolver=resolver_for("93.184.216.34")) == UNRESOLVABLE


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.0.0.5:8096/x", "http://169.254.169.254/latest", "http://[::1]/x", "http://[fe80::1]/"])
async def test_literal_private_addresses_are_refused_without_any_resolver(url):
    assert await destination_problem(url) == PRIVATE


async def test_the_operator_opt_in_allows_private_destinations():
    assert await destination_problem("http://10.0.0.5/x", allow_private=True) is None


async def test_an_allowed_host_is_exempt_and_only_that_host():
    private = resolver_for("10.0.0.5")
    assert await destination_problem("http://jf.lan:8096/a", allow_hosts=["JF.lan"], resolver=private) is None
    assert await destination_problem("http://other.lan/a", allow_hosts=["jf.lan"], resolver=private) == PRIVATE


@pytest.mark.parametrize("host, expected", [
    ("localhost", True), ("LOCALHOST", True), ("127.0.0.1", True), ("127.1.2.3", True), ("::1", True),
    ("10.0.0.5", False), ("localhost.evil.example", False), ("example.com", False), ("jellyfin", False),
])
def test_loopback_hosts(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize("url", [
    "https://jf.example", "https://jf.example:8443/base", "http://localhost:8096", "http://127.0.0.1:8096", "http://[::1]:8096",
    "https://10.0.0.5", "https://jellyfin",
])
def test_a_service_url_that_may_carry_a_key(url):
    assert service_url_problem("JELLYFIN_URL", url) is None


@pytest.mark.parametrize("url", ["http://jf.example", "http://10.0.0.5:8096", "http://jellyfin:8096", "http://192.168.1.2", "http://localhost.evil.example"])
def test_plaintext_to_anywhere_but_loopback_is_refused(url):
    assert "plaintext" in (service_url_problem("JELLYFIN_URL", url) or "")


@pytest.mark.parametrize("url, expected_none", [
    ("http://sonarr:8989", True), ("http://sonarr", True), ("http://sonarr.lan:8989", False), ("http://10.0.0.5:8989", False),
    ("http://sonarr.example.com", False), ("http://localhost:8989", True),
])
def test_a_bare_docker_name_is_allowed_only_when_asked_for(url, expected_none):
    assert (service_url_problem("SONARR_URL", url, bare_hostnames=True) is None) is expected_none
    if "sonarr:" in url or url == "http://sonarr":
        assert service_url_problem("SONARR_URL", url) is not None


def test_the_operator_opt_in_allows_plaintext_anywhere():
    assert service_url_problem("PELICAN_URL", "http://10.0.0.5", allow_http=True, opt_in="PELICAN_ALLOW_HTTP") is None
    refusal = service_url_problem("PELICAN_URL", "http://10.0.0.5", opt_in="PELICAN_ALLOW_HTTP")
    assert refusal is not None and "PELICAN_ALLOW_HTTP=1" in refusal


@pytest.mark.parametrize("url", ["ftp://host", "jf.example", "https://", "", "ws://host"])
def test_a_service_url_must_be_http_or_https_with_a_host(url):
    assert "http(s) URL with a host" in (service_url_problem("X_URL", url, allow_http=True) or "")


@pytest.mark.parametrize("url", ["https://user:pw@host", "https://:pw@host", "http://user@localhost"])
def test_a_service_url_must_not_embed_credentials(url):
    assert "embed credentials" in (service_url_problem("X_URL", url, allow_http=True) or "")


def test_the_setting_name_leads_every_message():
    assert service_url_problem("MY_URL", "http://a.example").startswith("refusing a plaintext MY_URL")
    assert urlpolicy.host_of("HTTPS://Example.COM:99/x") == "example.com"


def test_loopback_plaintext_can_be_switched_off_for_a_service_that_wants_the_flag_even_there():
    assert service_url_problem("P_URL", "http://127.0.0.1:8080") is None
    assert "plaintext" in service_url_problem("P_URL", "http://127.0.0.1:8080", loopback_http=False)
    assert service_url_problem("P_URL", "http://127.0.0.1:8080", loopback_http=False, allow_http=True) is None


def test_the_credentials_message_can_name_the_setting_the_key_belongs_in():
    assert "goes in P_KEY" in service_url_problem("P_URL", "https://u:p@host", key_setting="P_KEY")
