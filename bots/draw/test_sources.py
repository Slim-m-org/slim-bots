#!/usr/bin/env python3
"""The picture search, credit fields and decode step with scripted answers; run directly: python3 test_sources.py."""

import io
import json
import os
import sys

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


if __name__ == "__main__":
    run_module_tests(globals())
