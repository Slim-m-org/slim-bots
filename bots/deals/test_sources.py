#!/usr/bin/env python3
"""Source parsers against recorded GamerPower and Steam answers; run directly: python3 test_sources.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixtures  # noqa: E402
import prices  # noqa: E402
import sources  # noqa: E402


def web(**answers):
    def transport(url, headers):
        for key, answer in answers.items():
            if key in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return 404, b""
    return transport


def test_gamerpower_steam_items_become_offers():
    steam = sources.parse_gamerpower(fixtures.load("gamerpower_steam"))
    assert [(o.store, o.offer_id, o.worth) for o in steam] == [("steam", "3716", "$9.99")]
    assert steam[0].ends_at is None, "N/A means no end date"


def test_gamerpower_empty_answer_is_no_offers_and_expired_or_dlc_are_skipped():
    assert sources.parse_gamerpower({}) == []
    items = fixtures.load("gamerpower_steam")
    items[0]["status"] = "Expired"
    assert sources.parse_gamerpower(items) == []
    items[0].update(status="Active", type="DLC")
    assert sources.parse_gamerpower(items) == []


def test_steam_is_the_only_store_asked_for():
    asked = []

    def transport(url, headers):
        asked.append(url)
        return 200, fixtures.raw("gamerpower_steam")

    offers, error = sources.fetch_offers(transport)
    assert [o.store for o in offers] == ["steam"] and error == ""
    assert len(asked) == 1 and "platform=steam" in asked[0]
    assert not hasattr(sources, "parse_epic") and not any(w in sources.GAMERPOWER_URL for w in ("gog", "epic"))


def test_fetch_offers_reports_connection_and_garbage_errors_without_raising():
    assert sources.fetch_offers(web(platform=OSError("down")))[1] == "could not connect (OSError)"
    assert sources.fetch_offers(web(platform=(200, b"<html>")))[1] == "the answer is not JSON"
    assert sources.fetch_offers(web(platform=(500, b"")))[1] == "http 500"


def test_quote_reads_price_currency_and_discount():
    quote = prices.fetch_quote(web(appids=(200, fixtures.raw("appdetails_portal2_us"))), 620, "us")
    assert (quote.status, quote.name, quote.cents, quote.currency) == ("ok", "Portal 2", 999, "USD")


def test_quote_uses_minor_units_for_a_non_dollar_currency():
    quote = prices.fetch_quote(web(appids=(200, fixtures.raw("appdetails_portal2_jp"))), 620, "jp")
    assert (quote.cents, quote.currency) == (140000, "JPY"), "Steam reports yen times 100, so 1,400 yen is 140000"
    assert prices.format_cents(quote.cents, quote.currency) == "1400.00 JPY"


def test_quote_distinguishes_free_to_play_unavailable_and_errors():
    assert prices.fetch_quote(web(appids=(200, fixtures.raw("appdetails_free_to_play"))), 730, "us").status == "free"
    assert prices.fetch_quote(web(appids=(200, fixtures.raw("appdetails_unavailable"))), 1, "us").status == "unavailable"
    assert prices.fetch_quote(web(appids=(429, b"")), 1, "us").detail == "http 429"
    assert prices.fetch_quote(web(appids=(200, b"[]")), 1, "us").status == "error"


def test_app_id_and_price_parsing():
    assert prices.parse_app_id("620") == 620
    assert prices.parse_app_id("https://store.steampowered.com/app/1245620/ELDEN_RING/") == 1245620
    assert prices.parse_app_id("<store.steampowered.com/app/620>") == 620
    for bad in ("https://evil.example/app/620/", "portal", "0", "-5"):
        try:
            prices.parse_app_id(bad)
        except prices.PriceError:
            continue
        raise AssertionError(bad)
    assert [prices.parse_price_cents(t) for t in ("4.99", "$4.99", "4,99", "5", "1,234.56", "0.01")] == [499, 499, 499, 500, 123456, 1]
    for bad in ("free", "0", "-3", "99999999", ""):
        try:
            prices.parse_price_cents(bad)
        except prices.PriceError:
            continue
        raise AssertionError(bad)


def test_notify_rule_fires_once_per_level():
    rule = prices.should_notify
    assert rule(499, 500, None) and not rule(600, 500, None)
    assert not rule(499, 500, 499), "same price again stays quiet"
    assert rule(399, 500, 499), "a further drop pings again"
    assert not rule(450, 500, 399), "a smaller drop than already notified does not"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
