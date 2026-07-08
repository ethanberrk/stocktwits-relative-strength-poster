from src.source.rs_source import RSSource, _build_candidate, _EXCHANGE_PREFIX


def test_exchange_prefix_covers_stocktwits_strings():
    assert _EXCHANGE_PREFIX["NYSE"] == "NYSE"
    assert _EXCHANGE_PREFIX["NASDAQ"] == "NASDAQ"
    assert _EXCHANGE_PREFIX["NYSEAmerican"] == "AMEX"
    assert _EXCHANGE_PREFIX["AMEX"] == "AMEX"
    assert _EXCHANGE_PREFIX["NYSEArca"] == "AMEX"
    assert _EXCHANGE_PREFIX["BATS"] == "BATS"


def test_build_candidate_happy_path():
    quote = {"marketCap": 2_000_000_000, "regularMarketPrice": 84.99,
             "regularMarketChangePercent": 3.2, "fiftyTwoWeekHigh": 85.08,
             "quoteType": "EQUITY"}
    watch = {"watchlist_count": 15, "exchange": "NYSE"}
    c = _build_candidate("AAMI", "Acadian Asset Management Inc.", quote, watch)
    assert c is not None
    assert c.ticker == "AAMI" and c.watchers == 15
    assert c.exchange == "NYSE" and c.market_cap == 2_000_000_000


def test_build_candidate_drops_below_one_billion():
    quote = {"marketCap": 500_000_000, "regularMarketPrice": 10.0,
             "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("SMALL", "Small Co", quote, {"watchlist_count": 1,
                            "exchange": "NYSE"}) is None


def test_build_candidate_drops_missing_watchers():
    quote = {"marketCap": 2e9, "regularMarketPrice": 10.0,
             "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("NOWATCH", "No Watch", quote,
                            {"watchlist_count": None, "exchange": "NYSE"}) is None


def test_build_candidate_drops_unmappable_exchange():
    quote = {"marketCap": 2e9, "regularMarketPrice": 10.0,
             "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("OTCX", "Otc Co", quote,
                            {"watchlist_count": 5, "exchange": "OTC"}) is None


def test_build_candidate_drops_missing_marketcap():
    quote = {"regularMarketPrice": 10.0, "fiftyTwoWeekHigh": 10.0,
             "quoteType": "EQUITY"}
    assert _build_candidate("NOMC", "No MC", quote,
                            {"watchlist_count": 5, "exchange": "NYSE"}) is None


def test_build_candidate_drops_missing_price():
    quote = {"marketCap": 2e9, "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}
    assert _build_candidate("NOPRICE", "No Price", quote,
                            {"watchlist_count": 5, "exchange": "NYSE"}) is None


def test_fetch_candidates_wires_stages(monkeypatch):
    src = RSSource()
    monkeypatch.setattr(src, "_wsj_universe",
                        lambda: [("AAMI", "Acadian Asset Management Inc."),
                                 ("SMALL", "Small Co")])
    monkeypatch.setattr(src, "_yahoo_quotes", lambda tks: {
        "AAMI": {"marketCap": 3e9, "regularMarketPrice": 85.0,
                 "regularMarketChangePercent": 3.2, "fiftyTwoWeekHigh": 85.1,
                 "quoteType": "EQUITY"},
        "SMALL": {"marketCap": 5e8, "regularMarketPrice": 10.0,
                  "fiftyTwoWeekHigh": 10.0, "quoteType": "EQUITY"}})
    monkeypatch.setattr(src, "_watchers", lambda tks: {
        "AAMI": {"watchlist_count": 15, "exchange": "NYSE"},
        "SMALL": {"watchlist_count": 2, "exchange": "NYSE"}})
    cands = src.fetch_candidates()
    assert [c.ticker for c in cands] == ["AAMI"]  # SMALL dropped (<$1B)
    assert cands[0].watchers == 15


def test_fetch_candidates_raises_on_empty_universe(monkeypatch):
    import pytest
    from src.source.base import SourceError
    src = RSSource()
    monkeypatch.setattr(src, "_wsj_universe", lambda: [])
    with pytest.raises(SourceError):
        src.fetch_candidates()
