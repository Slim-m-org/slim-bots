#!/usr/bin/env python3
"""Picture sources (DuckDuckGo, Wikipedia, Commons, Google), their order and the decode steps with scripted answers; run directly: python3 test_sources.py."""

import io
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sources  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def web(routes, calls=None):
    """A scripted get: the first route whose key is in the URL answers with its (status, body)."""

    def get(url):
        if calls is not None:
            calls.append(url)
        for key, answer in routes.items():
            if key in url:
                return answer
        return 404, b""

    return get


DDG_PAGE = (200, b'<script>vqd="4-1234567890";</script>')
DDG_RESULTS = (200, json.dumps({"results": [
    {"image": "https://img/1.jpg", "title": "Obama one", "url": "https://site/1"},
    {"image": "https://img/2.jpg", "title": "Obama two", "url": "https://site/2"},
    {"title": "no image"},
]}).encode())
WIKI = (200, json.dumps({"query": {"pages": {"1": {"title": "Barack Obama", "index": 1, "thumbnail": {"source": "https://thumb/o.jpg"}, "fullurl": "https://en.wikipedia.org/wiki/Barack_Obama"}}}}).encode())
COMMONS = (200, json.dumps({"query": {"pages": {
    "1": {"title": "File:Obama.jpg", "index": 2, "imageinfo": [{"mime": "image/jpeg", "width": 4000, "thumburl": "https://thumb/c2.jpg", "descriptionurl": "https://commons/File:Obama.jpg"}]},
    "2": {"title": "File:Seal.svg", "index": 1, "imageinfo": [{"mime": "image/svg+xml", "width": 4000, "thumburl": "https://thumb/s.png", "descriptionurl": "https://commons/seal"}]},
    "3": {"title": "File:Tiny.png", "index": 3, "imageinfo": [{"mime": "image/png", "width": 90, "thumburl": "https://thumb/t.png", "descriptionurl": "https://commons/tiny"}]},
}}}).encode())


def test_duckduckgo_results_come_with_the_page_they_were_found_on():
    found = sources.duckduckgo_candidates(web({"duckduckgo.com/?": DDG_PAGE, "i.js": DDG_RESULTS}), "barack obama")
    assert [c["url"] for c in found] == ["https://img/1.jpg", "https://img/2.jpg"]
    assert found[0]["title"] == "Obama one" and found[0]["landing"] == "https://site/1" and found[0]["source"] == "duckduckgo"


def test_duckduckgo_sends_its_token_and_the_subject_and_a_bot_check_is_a_quiet_empty_result():
    calls, said = [], []
    sources.duckduckgo_candidates(web({"duckduckgo.com/?": DDG_PAGE, "i.js": DDG_RESULTS}, calls), "barack obama")
    assert "vqd=4-1234567890" in calls[1] and "q=barack+obama" in calls[1]
    assert sources.duckduckgo_candidates(web({"duckduckgo.com/?": (200, b"<html>no token</html>")}), "x", said.append) == [] and any("no token" in s for s in said)
    assert sources.duckduckgo_candidates(web({"duckduckgo.com/?": DDG_PAGE, "i.js": (202, b'{"challenge":{}}')}), "x", said.append) == [] and any("no results" in s for s in said)


def test_the_wikipedia_lead_image_is_a_candidate_with_its_page_and_no_licence_filter():
    found = sources.wikipedia_candidate(web({"en.wikipedia.org": WIKI}), "barack obama portrait")
    assert found == {"url": "https://thumb/o.jpg", "title": "Barack Obama", "creator": "", "license": "", "landing": "https://en.wikipedia.org/wiki/Barack_Obama", "source": "wikipedia"}
    assert sources.wikipedia_candidate(web({"en.wikipedia.org": (200, json.dumps({"query": {"pages": {"1": {"title": "No image", "index": 1}}}}).encode())}), "x") is None


def test_the_wikipedia_request_names_the_lead_file_and_the_subject_loses_drawing_words():
    calls = []
    sources.wikipedia_candidate(web({"en.wikipedia.org": WIKI}, calls), "hyperrealistic barack obama portrait")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    assert query["piprop"] == ["thumbnail|name"] and query["gsrsearch"] == ["barack obama"]
    assert sources.clean_subject("portrait") == "portrait"


def test_commons_files_are_namespace_6_picture_types_wide_enough_best_first():
    calls = []
    found = sources.commons_candidates(web({"commons.wikimedia.org": COMMONS}, calls), "barack obama")
    assert [c["title"] for c in found] == ["Obama.jpg"] and found[0]["landing"] == "https://commons/File:Obama.jpg" and found[0]["source"] == "commons"
    assert urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)["gsrnamespace"] == ["6"]


def test_google_results_use_the_key_in_the_request_only():
    answer = (200, json.dumps({"items": [{"link": "https://g/1.jpg", "title": "G", "image": {"contextLink": "https://page/g"}}]}).encode())
    calls = []
    found = sources.google_candidates(web({"googleapis.com": answer}, calls), "cat", "SECRET", "engine")
    assert found[0]["url"] == "https://g/1.jpg" and found[0]["landing"] == "https://page/g" and "searchType=image" in calls[0] and "key=SECRET" in calls[0]
    try:
        sources.google_candidates(web({"googleapis.com": (403, b"")}), "cat", "SECRET", "engine")
    except sources.SourceError as err:
        assert "SECRET" not in str(err)
    else:
        raise AssertionError("expected SourceError")


ALL = {"duckduckgo.com/?": DDG_PAGE, "i.js": DDG_RESULTS, "en.wikipedia.org": WIKI, "commons.wikimedia.org": COMMONS}


def test_duckduckgo_comes_first_then_wikipedia_then_commons_and_reading_stops_early():
    calls = []
    found = list(sources.find_pictures(web(ALL, calls), "barack obama"))
    assert [c["source"] for c in found] == ["duckduckgo", "duckduckgo", "wikipedia", "commons"]
    calls.clear()
    first = next(sources.find_pictures(web(ALL, calls), "barack obama"))
    assert first["source"] == "duckduckgo" and not any("wikipedia" in u or "commons" in u for u in calls)


def test_a_failing_source_is_skipped_quietly_and_google_leads_only_when_configured():
    said = []
    broken = dict(ALL, **{"duckduckgo.com/?": (500, b"")})
    found = list(sources.find_pictures(web(broken), "x", None, said.append))
    assert [c["source"] for c in found] == ["wikipedia", "commons"] and any("duckduckgo" in s for s in said)
    nothing = list(sources.find_pictures(web({}), "x"))
    assert nothing == []
    google = (200, json.dumps({"items": [{"link": "https://g/1.jpg", "title": "G"}]}).encode())
    found = list(sources.find_pictures(web(dict(ALL, **{"googleapis.com": google})), "x", ("k", "e")))
    assert found[0]["source"] == "google" and found[1]["source"] == "duckduckgo"


def test_the_credit_line_leaves_out_what_the_source_did_not_say():
    assert sources.credit_line({"title": "Obama one", "creator": "", "license": "", "landing": "https://site/1"}) == '"Obama one" https://site/1'
    assert sources.credit_line({"title": "A cat", "creator": "Sam", "license": "CC BY 2.0", "landing": "https://p"}) == '"A cat" by Sam (CC BY 2.0) https://p'
    assert sources.credit_line({"title": "T", "creator": "unknown", "license": "", "landing": ""}) == '"T"'


def test_the_user_agent_names_the_bot_and_a_picture_must_download_within_the_size_cap():
    assert "slimm-bot-draw" in sources.USER_AGENT and "github.com" in sources.USER_AGENT
    try:
        sources.fetch_picture(lambda url: (200, b"x" * (sources.MAX_PICTURE_BYTES + 1)), "u")
    except sources.SourceError as err:
        assert "too large" in str(err)
    else:
        raise AssertionError("expected SourceError")
    try:
        sources.fetch_picture(lambda url: (404, b""), "u")
    except sources.SourceError as err:
        assert "http 404" in str(err)


def test_a_reference_image_is_a_small_jpeg():
    from PIL import Image

    big = io.BytesIO()
    Image.new("RGB", (3000, 2000), (9, 9, 9)).save(big, "PNG")
    data, media_type = sources.reference_image(big.getvalue())
    assert media_type == "image/jpeg" and max(Image.open(io.BytesIO(data)).size) <= 768
    try:
        sources.reference_image(b"junk")
    except sources.SourceError:
        pass
    else:
        raise AssertionError("expected SourceError")


if __name__ == "__main__":
    run_module_tests(globals())
