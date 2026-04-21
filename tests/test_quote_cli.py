"""quote_cli 煙霧測試（不打真 SDK）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from typer.testing import CliRunner

from stock_order_api import quote_cli
from stock_order_api.realtime.models import Channel, Trade


class _FakeRealtimeClient:
    """模擬 RealtimeClient 的介面；subscribe 時立刻送兩筆假資料。"""

    def __init__(self, mode: Any) -> None:
        self.mode = mode
        self._data_handlers: list[Any] = []
        self._status_handlers: list[Any] = []
        self.closed = False
        self.unsubscribed = False

    # match RealtimeClient API ------------------------------------------
    def on_data(self, handler: Any) -> None:
        self._data_handlers.append(handler)

    def on_status(self, handler: Any) -> None:
        self._status_handlers.append(handler)

    def subscribe(
        self,
        channel: Channel,
        symbols: list[str],
        *,
        intraday_odd_lot: bool = False,
    ) -> list[Any]:
        for sym in symbols:
            dto = Trade(
                symbol=sym,
                price=Decimal("100.5"),
                size=10,
                time=__import__("datetime").datetime(2025, 1, 1, 9, 0, 0),
                bid_ask="bid",
                total_volume=123,
                is_trial=False,
            )
            for h in self._data_handlers:
                h(channel, dto)
        return []

    def unsubscribe_all(self) -> None:
        self.unsubscribed = True

    def close(self) -> None:
        self.closed = True

    def status(self) -> dict[str, Any]:
        return {"mode": "speed", "subscriptions": 0, "connections": []}


@pytest.fixture
def patch_bootstrap(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[_FakeRealtimeClient]]:
    created: list[_FakeRealtimeClient] = []

    def _fake_bootstrap(mode: Any) -> _FakeRealtimeClient:
        rt = _FakeRealtimeClient(mode)
        created.append(rt)
        return rt

    monkeypatch.setattr(quote_cli, "_bootstrap", _fake_bootstrap)
    yield created


def test_watch_jsonl(patch_bootstrap: list[_FakeRealtimeClient]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        [
            "watch",
            "trades",
            "2330",
            "2317",
            "--output",
            "jsonl",
            "--duration",
            "0.3",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln.startswith("{")]
    assert len(lines) >= 2
    parsed = [json.loads(ln) for ln in lines]
    assert {p["symbol"] for p in parsed} == {"2330", "2317"}
    assert all(p["_channel"] == "trades" for p in parsed)
    rt = patch_bootstrap[0]
    assert rt.unsubscribed is True
    assert rt.closed is True


def test_watch_table(patch_bootstrap: list[_FakeRealtimeClient]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        ["watch", "trades", "2330", "--duration", "0.3"],
    )
    assert result.exit_code == 0, result.output
    # table 渲染會輸出 symbol
    assert "2330" in result.output


def test_watch_csv(
    patch_bootstrap: list[_FakeRealtimeClient],
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        [
            "watch",
            "trades",
            "2330",
            "--output",
            "csv",
            "--duration",
            "0.3",
        ],
    )
    assert result.exit_code == 0, result.output
    csvs = list((tmp_path / "exports").glob("*_quote.csv"))
    assert len(csvs) == 1
    content = csvs[0].read_text(encoding="utf-8-sig")
    assert "2330" in content
    assert "_channel" in content


def test_watch_invalid_channel(patch_bootstrap: list[_FakeRealtimeClient]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        ["watch", "nope", "2330", "--duration", "0.1"],
    )
    assert result.exit_code != 0
    assert "未知 channel" in result.output or "nope" in result.output


def test_watch_invalid_output(patch_bootstrap: list[_FakeRealtimeClient]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        ["watch", "trades", "2330", "--output", "xml", "--duration", "0.1"],
    )
    assert result.exit_code != 0


def test_snapshot_ok(patch_bootstrap: list[_FakeRealtimeClient]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        ["snapshot", "2330", "--wait", "0.3"],
    )
    assert result.exit_code == 0, result.output
    assert "2330" in result.output


def test_snapshot_invalid_mode(patch_bootstrap: list[_FakeRealtimeClient]) -> None:
    runner = CliRunner()
    result = runner.invoke(
        quote_cli.app,
        ["snapshot", "2330", "--mode", "turbo"],
    )
    assert result.exit_code != 0
