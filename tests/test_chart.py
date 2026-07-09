from datetime import date

import pytest

from src import chart
from src.source.base import Candidate


def _c(ticker="ABCD", exchange="NASDAQ"):
    return Candidate(ticker=ticker, name="x", exchange=exchange, price=1.0,
                     pct_change_today=0.0, market_cap=2e9, week52_high=1.0,
                     security_type="EQUITY", watchers=1)


def _ohlc(n=60):
    rows, px = [], 20.0
    for i in range(n):
        o = px
        c = px * (1.02 if i % 3 else 0.985)
        rows.append([f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                     o, max(o, c) * 1.01, min(o, c) * 0.99, c])
        px = c
    return rows


def test_render_png_returns_png_bytes():
    png = chart._render_png(_c(), _ohlc())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(png) > 5_000


def test_fetch_history_appends_today_candle_when_stale(monkeypatch):
    def fake_get_json(url, **kw):
        if "history" in url:
            return {"data": [{"t": "2026-07-08", "o": 37.0, "h": 37.5,
                              "l": 35.1, "c": 37.47}]}
        if "api/quotes" in url:
            return {"data": {"p": 42.39, "o": 38.33, "h": 42.67, "l": 38.26}}
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(chart, "get_json", fake_get_json)
    rows = chart._fetch_history("TXG", today=date(2026, 7, 9))
    assert rows[-1] == ["2026-07-09", 38.33, 42.67, 38.26, 42.39]
    assert rows[0][0] == "2026-07-08"


def test_fetch_history_no_append_when_current(monkeypatch):
    def fake_get_json(url, **kw):
        if "history" in url:
            return {"data": [{"t": "2026-07-09", "o": 38.0, "h": 42.7,
                              "l": 38.0, "c": 42.39}]}
        raise AssertionError("quote endpoint must not be hit")

    monkeypatch.setattr(chart, "get_json", fake_get_json)
    rows = chart._fetch_history("TXG", today=date(2026, 7, 9))
    assert len(rows) == 1 and rows[0][0] == "2026-07-09"


def test_fetch_chart_png_raises_on_unavailable_history(monkeypatch):
    monkeypatch.setattr(chart, "get_json", lambda url, **kw: None)
    with pytest.raises(chart.ChartError):
        chart.fetch_chart_png(_c())


def test_fetch_chart_png_end_to_end(monkeypatch):
    monkeypatch.setattr(chart, "_fetch_history",
                        lambda ticker, today=None: _ohlc())
    png = chart.fetch_chart_png(_c())
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_legend_text_change_is_vs_previous_close():
    hist = [["2026-07-08", 20.0, 21.0, 19.5, 21.0],   # prev close 21.00
            ["2026-07-09", 22.0, 30.2, 20.9, 30.26]]  # gaps up to 22.00
    text = chart._legend_text(hist)
    assert "O 22.00" in text and "C 30.26" in text
    assert "+9.26 (+44.10%)" in text  # 30.26 vs prev CLOSE 21.00, not open


def test_legend_text_single_candle_uses_open():
    text = chart._legend_text([["2026-07-09", 20.0, 30.0, 20.0, 25.0]])
    assert "+5.00 (+25.00%)" in text
