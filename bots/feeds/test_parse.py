#!/usr/bin/env python3
"""Parsing, URL normalisation, keyword matching and the fetch guards; run directly: python3 test_parse.py."""


import db
import fixtures
import parse
import source
from slimbots.testing import run_module_tests


def test_rss_items_parse_with_decoded_text_and_a_date():
    parsed = parse.parse(fixtures.RSS)
    assert parsed.title == "Example Blog"
    first = parsed.entries[0]
    assert (first.key, first.title, first.link) == ("g-2", "Second & newer", "https://example.com/2")
    assert first.summary == "Hello world about rust"
    assert first.published == 1791280800000


def test_atom_prefers_the_alternate_link_and_falls_back_for_summary():
    parsed = parse.parse(fixtures.ATOM)
    b, a = parsed.entries
    assert b.link == "https://youtube.com/watch?v=b" and b.summary == "About B"
    assert a.link == "https://youtube.com/watch?v=a" and a.summary == "About A"
    assert a.published == 1791194400000


def test_rss_one_point_zero_items_are_read():
    parsed = parse.parse(fixtures.RDF)
    assert [e.title for e in parsed.entries] == ["One"]
    assert parsed.entries[0].key == "https://old.example/1"


def test_bad_input_raises_a_feed_error_not_a_crash():
    for body in (b"<html><body>hi</body></html>", b"<rss><channel>", b"", b"\xff\xfe junk", fixtures.BOMB):
        try:
            parse.parse(body)
        except parse.FeedError:
            continue
        raise AssertionError(f"{body!r} should have been refused")


def test_a_long_summary_is_cut_on_a_word_with_an_ellipsis():
    assert parse.clean("word " * 100, 20).endswith("…")
    assert len(parse.clean("word " * 100, 20)) <= 20


def test_urls_normalise_to_their_feed_address():
    n = source.normalize
    assert n("https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv") == "https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv"
    assert n("https://www.reddit.com/r/selfhosted/") == "https://www.reddit.com/r/selfhosted/.rss"
    assert n("https://mastodon.social/@gargron") == "https://mastodon.social/@gargron.rss"
    assert n("https://git.example.org/me/proj/releases") == "https://git.example.org/me/proj/releases.rss"
    assert n("https://github.com/me/proj/releases") == "https://github.com/me/proj/releases.atom"
    assert n("https://example.com/feed.xml") == "https://example.com/feed.xml"
    assert n("https://example.com/blog") == "https://example.com/blog"


def test_unusable_urls_are_refused_with_a_sentence():
    for url in ("ftp://example.com/x", "not a url", "https://www.youtube.com/@someone"):
        try:
            source.normalize(url)
        except source.SourceError as err:
            assert str(err)
            continue
        raise AssertionError(url)


def test_keyword_filter_includes_any_and_excludes_with_a_dash():
    m = db.keyword_match
    assert m("", "t", "s")
    assert m("Rust, python", "A post", "about RUST")
    assert not m("rust", "A post", "about go")
    assert not m("rust, -sponsored", "Rust sponsored", "")
    assert m("-sponsored", "plain", "")
    assert m("-", "plain", ""), "a bare dash excludes nothing"


def test_fetch_turns_every_failure_into_a_result():
    def serve(status, body=b"", headers=None):
        return lambda url, h: (status, headers or {}, body)

    assert source.fetch("u", transport=serve(200, fixtures.RSS, {"etag": "E"})).etag == "E"
    assert source.fetch("u", transport=serve(304)).status == "unchanged"
    assert source.fetch("u", transport=serve(500)).detail == "http 500"
    assert source.fetch("u", transport=serve(200, b"<html/>")).status == "error"
    assert "larger" in source.fetch("u", transport=serve(200, b"x" * (source.MAX_BODY + 1))).detail

    def boom(url, h):
        raise TimeoutError("secret")

    result = source.fetch("u", transport=boom)
    assert result.status == "error" and "secret" not in result.detail


def test_conditional_headers_are_sent_when_known():
    seen = []

    def spy(url, headers):
        seen.append(headers)
        return 304, {}, b""

    source.fetch("u", etag="E", last_modified="L", transport=spy)
    assert seen[0]["if-none-match"] == "E" and seen[0]["if-modified-since"] == "L"


def test_private_hosts_are_refused_unless_allowed():
    try:
        source.check_host("http://127.0.0.1/x", False)
    except source.SourceError:
        pass
    else:
        raise AssertionError("loopback must be refused")
    source.check_host("http://127.0.0.1/x", True)


if __name__ == "__main__":
    run_module_tests(globals())
