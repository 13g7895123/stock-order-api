"""CLI entrypoint：`uv run stock-order-quote <subcommand>`

子命令：
  watch <channel(s)> <symbols...>  持續訂閱並輸出
  snapshot <symbol>                一次性快照（下單 trades 訂閱幾秒後 dump）
通用參數：--mode speed|normal  --output table|jsonl|csv
"""

from __future__ import annotations

import json
import signal
import sys
import threading
import time
from typing import Any

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from stock_order_api.config import get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.logging_setup import setup_logging
from stock_order_api.realtime.client import RealtimeClient
from stock_order_api.realtime.errors import RealtimeError
from stock_order_api.realtime.models import Channel, RealtimeMode
from stock_order_api.utils.csv_export import export_rows

app = typer.Typer(add_completion=False, help="富邦即時行情 CLI")
console = Console()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bootstrap(mode: RealtimeMode) -> RealtimeClient:
    s = get_settings()
    setup_logging(log_dir=s.log_dir, level="INFO")
    client = FubonClient.instance(s)
    if not client.is_logged_in:
        client.login()
    return RealtimeClient(
        client=client,
        mode=mode,
        reconnect_base_sec=s.realtime_reconnect_base_sec,
        reconnect_max_sec=s.realtime_reconnect_max_sec,
        reconnect_max_attempts=s.realtime_reconnect_max,
        ring_buffer_size=s.realtime_ring_buffer,
        stats_interval_sec=s.realtime_stats_interval,
    )


def _parse_channels(raw: str) -> list[Channel]:
    out: list[Channel] = []
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        try:
            out.append(Channel(token))
        except ValueError as exc:
            raise typer.BadParameter(
                f"未知 channel：{token}（可選 "
                f"{', '.join(c.value for c in Channel)}）"
            ) from exc
    if not out:
        raise typer.BadParameter("請至少指定一個 channel")
    return out


def _dto_row(channel: Channel, dto: Any) -> dict[str, Any]:
    """把 DTO 轉成簡短的終端機可讀 dict。"""
    row = dto.model_dump(mode="json") if hasattr(dto, "model_dump") else dict(dto)
    row["_channel"] = channel.value
    return row


def _summarize(channel: Channel, dto: Any) -> dict[str, str]:
    """產生 table row（較短欄位）。"""
    d = dto.model_dump(mode="json") if hasattr(dto, "model_dump") else {}
    if channel == Channel.TRADES:
        return {
            "channel": "trades",
            "symbol": str(d.get("symbol", "")),
            "price": str(d.get("price", "")),
            "size": str(d.get("size", "")),
            "time": str(d.get("time", ""))[:19],
            "extra": f"vol={d.get('total_volume') or ''}",
        }
    if channel == Channel.BOOKS:
        bids = d.get("bids") or []
        asks = d.get("asks") or []
        b0 = bids[0] if bids else {}
        a0 = asks[0] if asks else {}
        return {
            "channel": "books",
            "symbol": str(d.get("symbol", "")),
            "price": f"b={b0.get('price','')}/a={a0.get('price','')}",
            "size": f"b={b0.get('size','')}/a={a0.get('size','')}",
            "time": str(d.get("time", ""))[:19],
            "extra": f"depth={len(bids)}x{len(asks)}",
        }
    if channel in (Channel.CANDLES, Channel.AGGREGATES):
        return {
            "channel": channel.value,
            "symbol": str(d.get("symbol", "")),
            "price": str(d.get("close", "")),
            "size": str(d.get("volume", "")),
            "time": str(d.get("time", ""))[:19],
            "extra": f"o={d.get('open','')} h={d.get('high','')} l={d.get('low','')}",
        }
    if channel == Channel.INDICES:
        return {
            "channel": "indices",
            "symbol": str(d.get("symbol", "")),
            "price": str(d.get("price", "")),
            "size": "-",
            "time": str(d.get("time", ""))[:19],
            "extra": f"Δ={d.get('change','')} ({d.get('change_percent','')}%)",
        }
    return {
        "channel": channel.value,
        "symbol": str(d.get("symbol", "")),
        "price": "",
        "size": "",
        "time": "",
        "extra": "",
    }


def _install_sigint(rt: RealtimeClient, stop_event: threading.Event) -> None:
    def _handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        stop_event.set()

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


def _graceful_close(rt: RealtimeClient) -> None:
    try:
        rt.unsubscribe_all()
    except Exception as exc:  # pragma: no cover
        console.print(f"[yellow]unsubscribe_all 警告：{exc}[/yellow]")
    try:
        rt.close()
    except Exception as exc:  # pragma: no cover
        console.print(f"[yellow]close 警告：{exc}[/yellow]")


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


@app.command()
def watch(
    channels: str = typer.Argument(
        ..., help="channel（可用逗號組合）：trades,books,aggregates,candles,indices"
    ),
    symbols: list[str] = typer.Argument(..., help="商品代號，可多個"),  # noqa: B008
    mode: str = typer.Option("speed", "--mode", help="speed|normal"),
    output: str = typer.Option("table", "--output", help="table|jsonl|csv"),
    intraday_odd_lot: bool = typer.Option(
        False, "--odd-lot", help="訂閱盤中零股"
    ),
    duration: float = typer.Option(
        0.0,
        "--duration",
        help="持續秒數（0 = 直到 Ctrl-C）",
    ),
) -> None:
    """持續訂閱並即時輸出。"""
    rt_mode = _parse_mode(mode)
    output = output.lower()
    if output not in ("table", "jsonl", "csv"):
        raise typer.BadParameter("--output 必須是 table|jsonl|csv")

    parsed_channels = _parse_channels(channels)
    rt = _bootstrap(rt_mode)

    stop = threading.Event()
    _install_sigint(rt, stop)

    if output == "jsonl":
        _run_jsonl(rt, parsed_channels, symbols, intraday_odd_lot, stop, duration)
    elif output == "csv":
        _run_csv(rt, parsed_channels, symbols, intraday_odd_lot, stop, duration)
    else:
        _run_table(rt, parsed_channels, symbols, intraday_odd_lot, stop, duration)


def _parse_mode(mode: str) -> RealtimeMode:
    mode = mode.strip().lower()
    if mode not in ("speed", "normal"):
        raise typer.BadParameter("--mode 必須是 speed 或 normal")
    return RealtimeMode(mode)


def _wait_until(stop: threading.Event, duration: float) -> None:
    if duration > 0:
        stop.wait(timeout=duration)
    else:
        while not stop.is_set():
            stop.wait(timeout=0.5)


def _subscribe_all(
    rt: RealtimeClient,
    channels: list[Channel],
    symbols: list[str],
    odd: bool,
) -> None:
    try:
        for ch in channels:
            rt.subscribe(ch, symbols, intraday_odd_lot=odd)
    except RealtimeError as exc:
        console.print(f"[red]訂閱失敗：{exc}[/red]")
        _graceful_close(rt)
        raise typer.Exit(code=2) from exc


def _run_jsonl(
    rt: RealtimeClient,
    channels: list[Channel],
    symbols: list[str],
    odd: bool,
    stop: threading.Event,
    duration: float,
) -> None:
    def _on(channel: Channel, dto: Any) -> None:
        row = _dto_row(channel, dto)
        sys.stdout.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        sys.stdout.flush()

    rt.on_data(_on)
    _subscribe_all(rt, channels, symbols, odd)
    _wait_until(stop, duration)
    _graceful_close(rt)


def _run_csv(
    rt: RealtimeClient,
    channels: list[Channel],
    symbols: list[str],
    odd: bool,
    stop: threading.Event,
    duration: float,
) -> None:
    rows: list[dict[str, Any]] = []
    lock = threading.Lock()

    def _on(channel: Channel, dto: Any) -> None:
        with lock:
            rows.append(_dto_row(channel, dto))

    rt.on_data(_on)
    _subscribe_all(rt, channels, symbols, odd)
    _wait_until(stop, duration)
    _graceful_close(rt)

    if not rows:
        console.print("[yellow](無資料可輸出)[/yellow]")
        return
    path = export_rows(rows, kind="quote")
    console.print(f"[green]CSV 已輸出：[/green]{path}")


def _run_table(
    rt: RealtimeClient,
    channels: list[Channel],
    symbols: list[str],
    odd: bool,
    stop: threading.Event,
    duration: float,
) -> None:
    latest: dict[tuple[str, str], dict[str, str]] = {}
    lock = threading.Lock()

    def _on(channel: Channel, dto: Any) -> None:
        row = _summarize(channel, dto)
        with lock:
            latest[(row["channel"], row["symbol"])] = row

    rt.on_data(_on)
    _subscribe_all(rt, channels, symbols, odd)

    def _render() -> Table:
        table = Table(title=f"Realtime / mode={rt.mode.value}", expand=True)
        table.add_column("channel")
        table.add_column("symbol")
        table.add_column("price")
        table.add_column("size")
        table.add_column("time")
        table.add_column("extra")
        with lock:
            for key in sorted(latest):
                r = latest[key]
                table.add_row(
                    r["channel"],
                    r["symbol"],
                    r["price"],
                    r["size"],
                    r["time"],
                    r["extra"],
                )
        return table

    with Live(_render(), console=console, refresh_per_second=4) as live:
        start = time.monotonic()
        while not stop.is_set():
            live.update(_render())
            stop.wait(timeout=0.25)
            if duration > 0 and time.monotonic() - start >= duration:
                break

    _graceful_close(rt)


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


@app.command()
def snapshot(
    symbol: str = typer.Argument(..., help="商品代號"),
    channel: str = typer.Option("trades", "--channel", help="訂閱哪個 channel"),
    wait: float = typer.Option(3.0, "--wait", help="等待幾秒後輸出最新一筆"),
    mode: str = typer.Option("speed", "--mode"),
) -> None:
    """短暫訂閱指定 channel，取到第一筆資料就結束。"""
    rt_mode = _parse_mode(mode)
    channels = _parse_channels(channel)
    rt = _bootstrap(rt_mode)

    latest: dict[str, Any] = {}
    got = threading.Event()

    def _on(ch: Channel, dto: Any) -> None:
        latest["channel"] = ch.value
        latest["data"] = _dto_row(ch, dto)
        got.set()

    rt.on_data(_on)
    try:
        _subscribe_all(rt, channels, [symbol], False)
        got.wait(timeout=wait)
    finally:
        _graceful_close(rt)

    if not latest:
        console.print(f"[yellow]{wait}s 內未收到 {symbol} 的 {channel} 資料[/yellow]")
        raise typer.Exit(code=1)

    console.print_json(data=latest["data"])


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status(
    symbols: list[str] = typer.Argument(..., help="訂閱 trades 後立刻 dump 狀態"),  # noqa: B008
    mode: str = typer.Option("speed", "--mode"),
) -> None:
    """快速檢查連線／訂閱分片（debug 用）。"""
    rt_mode = _parse_mode(mode)
    rt = _bootstrap(rt_mode)
    try:
        rt.subscribe(Channel.TRADES, symbols)
        time.sleep(1.0)
        console.print_json(data=rt.status())
    finally:
        _graceful_close(rt)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
