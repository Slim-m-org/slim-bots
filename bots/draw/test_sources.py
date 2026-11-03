#!/usr/bin/env python3
"""The picture search, credit fields and decode step with scripted answers; run directly: python3 test_sources.py."""

import io
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sources  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

RESULT = {
    "results": [
        {"thumbnail": "https://t/1.jpg", "title": "A cat", "creator": "Sam", "license": "by", "license_version": "2.0", "foreign_landing_url": "https://page/1"},
        {"thumbnail": None, "title": "no thumb", "license": "by"},
        {"thumbnail": "https://t/3.jpg", "title": "no licence"},
    ]
}


def test_search_keeps_only_results_with_a_thumbnail_and_a_licence_and_asks_for_non_mature():
    urls = []

    def get(url):
        urls.append(url)
        return 200, json.dumps(RESULT).encode()

    found = sources.search(get, "cat")
    assert found == [{"url": "https://t/1.jpg", "title": "A cat", "creator": "Sam", "license": "CC BY 2.0", "landing": "https://page/1"}]
    assert "mature=false" in urls[0] and "q=cat" in urls[0]


def test_search_failures_are_sentences():
    for status, words in ((429, "rate limited"), (500, "http 500")):
        try:
            sources.search(lambda url: (status, b""), "cat")
        except sources.SourceError as err:
            assert words in str(err)
        else:
            raise AssertionError("no error")
    try:
        sources.search(lambda url: (200, b"<html>"), "cat")
    except sources.SourceError as err:
        assert "could not read" in str(err)


def test_a_missing_or_oversized_picture_is_refused():
    for answer in ((404, b""), (200, b"x" * (sources.MAX_PICTURE_BYTES + 1))):
        try:
            sources.fetch_picture(lambda url, a=answer: a, "https://t/1.jpg")
        except sources.SourceError:
            continue
        raise AssertionError("accepted")


def test_decode_without_pillow_says_so():
    saved = sys.modules.get("PIL")
    sys.modules["PIL"] = None
    try:
        sources.decode_gray(b"x", 96)
    except sources.SourceError as err:
        assert "Pillow" in str(err)
    else:
        raise AssertionError("no error")
    finally:
        if saved is None:
            sys.modules.pop("PIL", None)
        else:
            sys.modules["PIL"] = saved


def test_decode_fits_the_picture_in_the_box_and_returns_gray_rows():
    try:
        from PIL import Image
    except ImportError:
        print("skipped: Pillow is not installed here")
        return
    buffer = io.BytesIO()
    Image.new("RGB", (400, 200), (255, 0, 0)).save(buffer, "PNG")
    grid, width, height = sources.decode_gray(buffer.getvalue(), 96)
    assert (width, height) == (96, 48) and len(grid) == 48 and len(grid[0]) == 96 and 0 < grid[0][0] < 255
    try:
        sources.decode_gray(b"not a picture", 96)
    except sources.SourceError as err:
        assert "could not be read" in str(err)
    else:
        raise AssertionError("no error")


def wiki_get(pages, files, calls=None):
    """A scripted get: a Wikipedia search answering `pages` and a Commons lookup answering `files` by file name."""

    def get(url):
        if calls is not None:
            calls.append(url)
        if "en.wikipedia.org" in url:
            return 200, json.dumps({"query": {"pages": {str(i): p for i, p in enumerate(pages)}}}).encode()
        if "commons.wikimedia.org" in url:
            for name, meta in files.items():
                if name.replace(" ", "+") in url or name.replace(" ", "%20") in url or name in url:
                    return 200, json.dumps({"query": {"pages": {"1": {"imageinfo": [{"extmetadata": meta}]}}}}).encode()
            return 200, json.dumps({"query": {"pages": {"-1": {"missing": ""}}}}).encode()
        return 404, b""

    return get


def page(title, image, index=1):
    return {"title": title, "index": index, "pageimage": image, "thumbnail": {"source": f"https://upload/{image}"}, "fullurl": f"https://en.wikipedia.org/wiki/{title}"}


def meta(licence, artist="<a href='x'>Pete &amp; Co</a>", nonfree=None):
    return {"LicenseShortName": {"value": licence}, "Artist": {"value": artist}, **({"NonFree": {"value": nonfree}} if nonfree else {})}


def test_the_subject_loses_drawing_words_before_the_encyclopedia_search():
    assert sources.clean_subject("hyperrealistic barack obama portrait") == "barack obama"
    assert sources.clean_subject("portrait") == "portrait"


def test_a_freely_licensed_lead_image_becomes_a_credited_candidate_with_plain_text_credit():
    get = wiki_get([page("Barack Obama", "Obama.jpg")], {"Obama.jpg": meta("CC BY-SA 4.0")})
    found = sources.wikipedia_candidate(get, "barack obama portrait")
    assert found == {"url": "https://upload/Obama.jpg", "title": "Barack Obama", "creator": "Pete & Co", "license": "CC BY-SA 4.0", "landing": "https://en.wikipedia.org/wiki/Barack Obama"}


def test_public_domain_is_accepted_and_non_free_or_missing_files_are_skipped_in_favour_of_the_next_article():
    pages = [page("A", "a.jpg", 1), page("B", "b.jpg", 2), page("C", "c.jpg", 3)]
    files = {"a.jpg": meta("Fair use", nonfree="true"), "c.jpg": meta("Public domain")}
    found = sources.wikipedia_candidate(wiki_get(pages, files), "x")
    assert found["title"] == "C" and found["license"] == "Public domain"
    assert sources.wikipedia_candidate(wiki_get([page("A", "a.jpg")], {"a.jpg": meta("All rights reserved")}), "x") is None
    assert sources.wikipedia_candidate(wiki_get([page("A", "a.jpg")], {}), "x") is None
    assert sources.wikipedia_candidate(wiki_get([{"title": "No image", "index": 1}], {}), "x") is None


def test_pictures_come_from_wikipedia_first_and_openverse_is_only_asked_when_it_has_nothing():
    asked = []
    get = wiki_get([page("Barack Obama", "o.jpg")], {"o.jpg": meta("CC0")})
    original = sources.search
    sources.search = lambda g, q, limit=8: asked.append(q) or [{"url": "u", "title": "t", "creator": "c", "license": "CC BY 2.0", "landing": "l"}]
    try:
        found = sources.find_pictures(get, "barack obama")
        assert [c["source"] for c in found] == ["wikipedia"] and asked == []
        found = sources.find_pictures(wiki_get([], {}), "barack obama")
        assert [c["source"] for c in found] == ["openverse"] and asked == ["barack obama"]
        broken = lambda url: (500, b"")
        assert [c["source"] for c in sources.find_pictures(broken, "x")] == ["openverse"]
    finally:
        sources.search = original


def test_the_openverse_token_never_reaches_wikipedia_and_every_call_names_the_bot():
    wiki_urls, verse = [], []
    plain = wiki_get([], {}, wiki_urls)
    original = sources.search
    sources.search = lambda g, q, limit=8: verse.append(g) or []
    try:
        carrying = lambda url: (200, b"{}")
        sources.find_pictures(plain, "x", carrying)
    finally:
        sources.search = original
    assert verse == [carrying] and all("wikipedia" in u or "commons" in u for u in wiki_urls)
    assert "slimm-bot-draw" in sources.USER_AGENT and "github.com" in sources.USER_AGENT


# Trimmed from live api.php answers (2026-11-03): the search only names the lead file when piprop asks for `name`.
LIVE_SEARCH = {"batchcomplete": "", "continue": {"gsroffset": 3}, "query": {"pages": {"534366": {
    "pageid": 534366, "ns": 0, "title": "Barack Obama", "index": 1,
    "thumbnail": {"source": "https://thumb.wikimedia.org/wikipedia/commons/thumb/8/8d/President_Barack_Obama.jpg/960px-President_Barack_Obama.jpg?utm_source=en.wikipedia.org", "width": 800, "height": 999},
    "pageimage": "President_Barack_Obama.jpg", "fullurl": "https://en.wikipedia.org/wiki/Barack_Obama"}}}}
LIVE_COMMONS = {"batchcomplete": "", "query": {"normalized": [{"from": "File:President_Barack_Obama.jpg", "to": "File:President Barack Obama.jpg"}], "pages": {"23956389": {
    "pageid": 23956389, "ns": 6, "title": "File:President Barack Obama.jpg", "imagerepository": "local",
    "imageinfo": [{"extmetadata": {
        "Artist": {"value": "Official White House Photo by <a href=\"https://en.wikipedia.org/wiki/Pete_Souza\" class=\"extiw\" title=\"w:Pete Souza\">Pete Souza</a>", "source": "commons-desc-page"},
        "LicenseShortName": {"value": "Public domain", "source": "commons-desc-page", "hidden": ""}}}]}}}}


def live_get(urls):
    def get(url):
        urls.append(url)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if "en.wikipedia.org" in url:
            page = dict(LIVE_SEARCH["query"]["pages"]["534366"])
            if "name" not in query["piprop"][0].split("|"):
                page.pop("pageimage")
            return 200, json.dumps({"query": {"pages": {"534366": page}}}).encode()
        return 200, json.dumps(LIVE_COMMONS).encode()

    return get


def test_the_live_shapes_resolve_to_the_public_domain_lead_image_regression():
    urls = []
    found = sources.wikipedia_candidate(live_get(urls), "barack obama")
    assert found["title"] == "Barack Obama" and found["license"] == "Public domain" and found["creator"] == "Official White House Photo by Pete Souza"
    assert found["url"].startswith("https://thumb.wikimedia.org/") and found["landing"] == "https://en.wikipedia.org/wiki/Barack_Obama"
    assert "piprop=thumbnail%7Cname" in urls[0] or "piprop=thumbnail|name" in urls[0]


def test_why_a_source_was_passed_over_is_reported():
    said = []
    sources.wikipedia_candidate(wiki_get([page("A", "a.jpg"), {"title": "No image", "index": 2}], {"a.jpg": meta("Fair use", nonfree="true")}), "x", said.append)
    assert any("a.jpg" in s and "non-free" in s for s in said) and any("no lead image" in s for s in said)
    said.clear()
    sources.wikipedia_candidate(wiki_get([page("A", "a.jpg")], {}), "x", said.append)
    assert any("not on Commons" in s for s in said)
    said.clear()
    original = sources.search
    sources.search = lambda g, q, limit=8: []
    try:
        sources.find_pictures(lambda url: (500, b""), "x", None, said.append)
    finally:
        sources.search = original
    assert any("wikipedia: wikipedia answered http 500" in s for s in said) and any("using openverse" in s for s in said)


if __name__ == "__main__":
    run_module_tests(globals())
