import pytest
import requests
from src import chart
from src.source.base import Candidate


def _c(ticker="ABCD", exchange="NASDAQ"):
    return Candidate(ticker=ticker, name="x", exchange=exchange, price=1.0,
                     pct_change_today=0.0, market_cap=2e9, week52_high=1.0,
                     security_type="EQUITY", watchers=1)


def test_request_args_builds_exchange_prefixed_symbol():
    url, body, headers = chart._request_args(_c("ABCD", "NASDAQ"), "KEY")
    assert body["symbol"] == "NASDAQ:ABCD"
    assert body["session"] == "regular"
    assert body["range"] == "1Y"
    assert headers["x-api-key"] == "KEY"


def test_fetch_chart_png_returns_bytes(monkeypatch):
    class Resp:
        status_code = 200
        content = b"PNGDATA"
    monkeypatch.setattr(chart.requests, "post", lambda *a, **k: Resp())
    assert chart.fetch_chart_png(_c(), "KEY") == b"PNGDATA"


def test_fetch_chart_png_raises_on_non_200(monkeypatch):
    class Resp:
        status_code = 500
        content = b""
    monkeypatch.setattr(chart.requests, "post", lambda *a, **k: Resp())
    with pytest.raises(chart.ChartError):
        chart.fetch_chart_png(_c(), "KEY")
