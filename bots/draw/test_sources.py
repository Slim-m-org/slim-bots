#!/usr/bin/env python3
"""Picture sources (Bing, Wikipedia, Commons, Google), their order and the decode steps with scripted answers; run directly: python3 test_sources.py."""

import io
import json
import os
import sys
import urllib.parse

import httpx

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


# Shape recorded from www.bing.com/images/async (2026-11-03): each result is a `m="{...}"` attribute of HTML-escaped JSON.
BING_PAGE = (200, (
    '<style type="text/css">.mc_fh{height:100%}</style><ul class="dgControl_list">'
    '<li data-idx="0"><a class="iusc" m="{&quot;cid&quot;:&quot;x1&quot;,&quot;purl&quot;:&quot;https://news.example/obama-story&quot;,'
    '&quot;murl&quot;:&quot;https://news.example/uploads/Barack-Obama-1.jpg&quot;,&quot;turl&quot;:&quot;https://ts4.mm.bing.net/th?id=OIF.a&amp;pid=15.1&quot;,'
    '&quot;md5&quot;:&quot;5086&quot;,&quot;t&quot;:&quot;Barack Obama Just Handed the World A Big Clue &amp; More&quot;,&quot;desc&quot;:&quot;d&quot;}" href="/images/search?q=x"></a></li>'
    '<li data-idx="1"><a class="iusc" m="{&quot;purl&quot;:&quot;https://other.example/p&quot;,&quot;murl&quot;:&quot;https://other.example/img/2.png&quot;,&quot;t&quot;:&quot;Second&quot;}"></a></li>'
    '<li data-idx="2"><a class="iusc" m="{&quot;purl&quot;:&quot;https://bad.example/p&quot;,&quot;murl&quot;:&quot;data:image/gif;base64,AAAA&quot;,&quot;t&quot;:&quot;not http&quot;}"></a></li>'
    '<li data-idx="3"><a class="iusc" m="{not json}"></a></li></ul>'
).encode())
WIKI = (200, json.dumps({"query": {"pages": {"1": {"title": "Barack Obama", "index": 1, "thumbnail": {"source": "https://thumb/o.jpg"}, "fullurl": "https://en.wikipedia.org/wiki/Barack_Obama"}}}}).encode())
COMMONS = (200, json.dumps({"query": {"pages": {
    "1": {"title": "File:Obama.jpg", "index": 2, "imageinfo": [{"mime": "image/jpeg", "width": 4000, "thumburl": "https://thumb/c2.jpg", "descriptionurl": "https://commons/File:Obama.jpg"}]},
    "2": {"title": "File:Seal.svg", "index": 1, "imageinfo": [{"mime": "image/svg+xml", "width": 4000, "thumburl": "https://thumb/s.png", "descriptionurl": "https://commons/seal"}]},
    "3": {"title": "File:Tiny.png", "index": 3, "imageinfo": [{"mime": "image/png", "width": 90, "thumburl": "https://thumb/t.png", "descriptionurl": "https://commons/tiny"}]},
}}}).encode())


def test_bing_results_carry_the_full_size_picture_its_title_and_the_page_it_was_on():
    found = sources.bing_candidates(web({"bing.com/images/async": BING_PAGE}), "barack obama")
    assert [c["url"] for c in found] == ["https://news.example/uploads/Barack-Obama-1.jpg", "https://other.example/img/2.png"]
    assert found[0]["title"] == "Barack Obama Just Handed the World A Big Clue & More" and found[0]["landing"] == "https://news.example/obama-story"
    assert found[0]["source"] == "bing" and found[0]["license"] == "" and found[0]["creator"] == ""


def test_bing_is_asked_for_the_subject_with_the_async_parameters_and_failures_are_quiet():
    calls, said = [], []
    sources.bing_candidates(web({"bing.com/images/async": BING_PAGE}, calls), "barack obama")
    query = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    assert query["q"] == ["barack obama"] and query["mmasync"] == ["1"] and query["first"] == ["0"]
    assert sources.bing_candidates(web({"bing.com/images/async": (503, b"")}), "x", said.append) == [] and any("bing: http 503" in s for s in said)
    assert sources.bing_candidates(web({"bing.com/images/async": (200, b"<html>nothing</html>")}), "x", said.append) == [] and any("no results" in s for s in said)


def test_the_picture_request_uses_the_bots_own_user_agent_and_nothing_pretends_to_be_a_browser():
    assert "slimm-bot-draw" in sources.USER_AGENT and "Mozilla" not in sources.USER_AGENT
    assert "duckduckgo" not in open(sources.__file__).read().lower()


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


ALL = {"bing.com/images/async": BING_PAGE, "en.wikipedia.org": WIKI, "commons.wikimedia.org": COMMONS}


def test_bing_comes_first_then_wikipedia_then_commons_and_reading_stops_early():
    calls = []
    found = list(sources.find_pictures(web(ALL, calls), "barack obama"))
    assert [c["source"] for c in found] == ["bing", "bing", "wikipedia", "commons"]
    calls.clear()
    first = next(sources.find_pictures(web(ALL, calls), "barack obama"))
    assert first["source"] == "bing" and not any("wikipedia" in u or "commons" in u for u in calls)


def test_a_failing_source_is_skipped_quietly_and_google_leads_only_when_configured():
    said = []
    broken = dict(ALL, **{"bing.com/images/async": (500, b"")})
    found = list(sources.find_pictures(web(broken), "x", None, said.append))
    assert [c["source"] for c in found] == ["wikipedia", "commons"] and any("bing" in s for s in said)
    assert list(sources.find_pictures(web({}), "x")) == []
    google = (200, json.dumps({"items": [{"link": "https://g/1.jpg", "title": "G"}]}).encode())
    found = list(sources.find_pictures(web(dict(ALL, **{"googleapis.com": google})), "x", ("k", "e")))
    assert found[0]["source"] == "google" and found[1]["source"] == "bing"


# Shape from SerpAPI's google_images engine documentation: images_results items with original, thumbnail, title, source and the page `link`.
SERP_OK = (200, json.dumps({"search_metadata": {"status": "Success"}, "images_results": [
    {"position": 1, "thumbnail": "https://serpapi.com/t/1.jpg", "source": "nbcnews.com", "title": "Barack Obama - NBC News", "link": "https://www.nbcnews.com/obama", "original": "https://media.nbcnews.com/obama.jpg", "original_width": 1600, "original_height": 900, "is_product": False},
    {"position": 2, "thumbnail": "https://serpapi.com/t/2.jpg", "source": "wiki.example", "title": "", "link": "https://wiki.example/obama", "original": "https://wiki.example/o.png"},
    {"position": 3, "thumbnail": "https://serpapi.com/t/3.jpg", "title": "no original", "link": "https://x"},
]}).encode())
SERP_BAD_KEY = (401, json.dumps({"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}).encode())


def test_serpapi_results_carry_the_original_its_page_and_the_thumbnail_as_a_fallback():
    found = sources.serpapi_candidates(web({"serpapi.com/search.json": SERP_OK}), "barack obama", "KEY123")
    assert [c["url"] for c in found] == ["https://media.nbcnews.com/obama.jpg", "https://wiki.example/o.png"]
    assert found[0]["title"] == "Barack Obama - NBC News" and found[0]["landing"] == "https://www.nbcnews.com/obama" and found[0]["source"] == "serpapi"
    assert found[0]["fallback"] == "https://serpapi.com/t/1.jpg" and found[1]["title"] == "wiki.example"


def test_serpapi_is_asked_with_the_google_images_engine_the_subject_and_the_key_and_counts_the_call():
    calls, spent = [], []
    sources.serpapi_candidates(web({"serpapi.com/search.json": SERP_OK}, calls), "barack obama", "KEY123", None, lambda: spent.append(1))
    query = urllib.parse.parse_qs(urllib.parse.urlparse(calls[0]).query)
    assert query["engine"] == ["google_images"] and query["q"] == ["barack obama"] and query["api_key"] == ["KEY123"] and query["safe"] == ["off"] and spent == [1]


def test_a_serpapi_error_is_a_source_error_that_never_carries_the_key():
    leaky = (401, json.dumps({"error": "Invalid API key KEY123 rejected"}).encode())
    for answer in (SERP_BAD_KEY, leaky, (429, b""), (200, b"not json")):
        try:
            sources.serpapi_candidates(web({"serpapi.com/search.json": answer}), "x", "KEY123")
        except sources.SourceError as err:
            assert "KEY123" not in str(err) and "serpapi" in str(err)
        else:
            raise AssertionError("expected SourceError")


def test_serpapi_leads_only_when_a_key_is_set_and_its_failure_is_skipped_and_noted_without_the_key():
    said, spent = [], []
    found = list(sources.find_pictures(web(dict(ALL, **{"serpapi.com/search.json": SERP_OK})), "x", None, said.append, "KEY123", lambda: spent.append(1)))
    assert found[0]["source"] == "serpapi" and found[-1]["source"] == "commons" and spent == [1]
    said.clear()
    found = list(sources.find_pictures(web(dict(ALL, **{"serpapi.com/search.json": SERP_BAD_KEY})), "x", None, said.append, "KEY123"))
    assert [c["source"] for c in found][0] == "bing" and any("serpapi" in s for s in said) and not any("KEY123" in s for s in said)
    assert list(sources.find_pictures(web(ALL), "x"))[0]["source"] == "bing"


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


def public_get(handler):
    """`http_get_public` against a scripted transport, returning its answer or the SourceError sentence."""
    seen = []
    sources.TRANSPORT = httpx.MockTransport(lambda request: seen.append(str(request.url)) or handler(request))
    return seen


def picture_answer(url):
    try:
        return sources.http_get_public(url)
    except sources.SourceError as err:
        return str(err)
    finally:
        sources.TRANSPORT = None


def test_a_picture_on_a_public_host_downloads_with_the_bots_user_agent():
    agents = []
    seen = public_get(lambda request: agents.append(request.headers["user-agent"]) or httpx.Response(200, content=b"png"))
    assert picture_answer("https://93.184.216.34/cat.png") == (200, b"png")
    assert seen == ["https://93.184.216.34/cat.png"] and agents == [sources.USER_AGENT]


def test_a_picture_url_that_points_into_the_lan_is_refused_before_any_request():
    seen = []
    for url in ("http://127.0.0.1/cat.png", "http://192.168.1.1/admin", "http://169.254.169.254/latest/meta-data"):
        public_get(lambda request: seen.append(str(request.url)) or httpx.Response(200, content=b"secret"))
        assert picture_answer(url) == "the picture is not on a public host"
    assert seen == []


def test_a_picture_that_redirects_into_the_lan_is_refused_at_that_hop():
    seen = public_get(lambda request: httpx.Response(302, headers={"location": "http://10.0.0.5:8123/reboot"}))
    assert picture_answer("https://93.184.216.34/cat.png") == "the picture is not on a public host"
    assert seen == ["https://93.184.216.34/cat.png"]


def test_a_picture_past_the_size_cap_or_from_a_dead_host_is_a_sentence_not_a_traceback():
    public_get(lambda request: httpx.Response(200, content=b"x" * (sources.MAX_PICTURE_BYTES + 1)))
    assert picture_answer("https://93.184.216.34/big.png") == "the picture is too large"

    def refuse(request):
        raise httpx.ConnectError("refused")

    public_get(refuse)
    assert picture_answer("https://93.184.216.34/dead.png") == "could not connect (ServiceUnreachable)"


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
