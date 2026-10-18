#!/usr/bin/env python3
"""Source parsers against recorded Epic, GamerPower and Steam answers; run directly: python3 test_sources.py."""

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


def test_epic_keeps_only_games_free_right_now():
    offers = sources.parse_epic(fixtures.load("epic_free_games"))
    assert [o.title for o in offers] == ["Bad Cheese", "Agent A: A puzzle in disguise"], "upcoming and discounted-but-paid entries are skipped"
    assert offers[0].url == "https://store.epicgames.com/en-US/p/bad-cheese-4256d9" and offers[0].store == "epic"
    assert offers[0].ends_at.isoformat() == "2026-10-22T15:00:00+00:00"


def test_epic_ignores_add_ons_and_a_missing_window():
    data = fixtures.load("epic_free_games")
    for element in data["data"]["Catalog"]["searchStore"]["elements"]:
        if element["title"] == "Bad Cheese":
            element["offerType"] = "ADD_ON"
    assert [o.title for o in sources.parse_epic(data)] == ["Agent A: A puzzle in disguise"]


def test_gamerpower_steam_and_gog_items_become_offers():
    steam = sources.parse_gamerpower(fixtures.load("gamerpower_steam"), "steam")
    assert [(o.store, o.offer_id, o.worth) for o in steam] == [("steam", "3716", "$9.99")]
    assert steam[0].ends_at is None, "N/A means no end date"
    gog = sources.parse_gamerpower(fixtures.load("gamerpower_gog"), "gog")
    assert gog[0].title.startswith("Example Quest") and gog[0].url.endswith("example-quest-gog-giveaway")


def test_gamerpower_empty_answer_is_no_offers_and_expired_or_dlc_are_skipped():
    assert sources.parse_gamerpower(fixtures.load("gamerpower_gog_empty"), "gog") == []
    items = fixtures.load("gamerpower_steam")
    items[0]["status"] = "Expired"
    assert sources.parse_gamerpower(items, "steam") == []
    items[0].update(status="Active", type="DLC")
    assert sources.parse_gamerpower(items, "steam") == []


def test_fetch_offers_isolates_a_failing_source():
    transport = web(
        freeGamesPromotions=(500, b""), **{"platform=steam": (200, fixtures.raw("gamerpower_steam"))},
        **{"platform=gog": (201, fixtures.raw("gamerpower_gog_empty"))},
    )
    offers, errors = sources.fetch_offers(["epic", "steam", "gog"], transport)
    assert [o.store for o in offers] == ["steam"] and errors == {"epic": "http 500"}


def test_fetch_offers_reports_connection_and_garbage_errors_without_raising():
    _, errors = sources.fetch_offers(["epic", "steam"], web(freeGamesPromotions=OSError("down"), **{"platform=steam": (200, b"<html>")}))
    assert errors == {"epic": "could not connect (OSError)", "steam": "the answer is not JSON"}


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
