"""CLI entrypoint：`uv run stock-order-account <subcommand>`

子命令：
  login, inventories, unrealized, realized, buying-power, settlements, maintenance
通用參數：--output / --no-cache / --account
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from stock_order_api.audit.store import AuditStore
from stock_order_api.config import get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.fubon.errors import FubonError
from stock_order_api.fubon.stock_account import StockAccount
from stock_order_api.logging_setup import setup_logging
from stock_order_api.utils.csv_export import export_rows, models_to_rows

app = typer.Typer(add_completion=False, help="富邦帳務 CLI")
console = Console()


# ---------------------------------------------------------------------------
# 共用 bootstrap
# ---------------------------------------------------------------------------


def _bootstrap(account: str | None = None) -> tuple[FubonClient, StockAccount, AuditStore]:
    s = get_settings()
    setup_logging(log_dir=s.log_dir, level="INFO")
    audit = AuditStore(s.audit_db_path)
    client = FubonClient.instance(s)
    if not client.is_logged_in:
        client.login()
    if account:
        branch, _, acct = account.partition("-")
        client.select_account(branch, acct)
    svc = StockAccount(client=client, audit=audit)
    return client, svc, audit


def _print(data: Any, output: str, kind: str) -> None:
    if output == "json":
        console.print_json(data=_jsonable(data))
    elif output == "csv":
        path = _write_csv(data, kind)
        console.print(f"[green]CSV 已輸出：[/green]{path}")
    else:
        _print_table(data)


def _jsonable(data: Any) -> Any:
    if isinstance(data, list):
        return [_one(x) for x in data]
    return _one(data)


def _one(x: Any) -> Any:
    if hasattr(x, "model_dump"):
        return x.model_dump(mode="json")
    return x


def _write_csv(data: Any, kind: str) -> Path:
    rows = models_to_rows(data if isinstance(data, list) else [data])
    return export_rows(rows, kind=kind)


def _print_table(data: Any) -> None:
    rows = data if isinstance(data, list) else [data]
    if not rows:
        console.print("[yellow](無資料)[/yellow]")
        return
    rows_d = models_to_rows(rows)
    cols = list(rows_d[0].keys())
    table = Table(show_header=True, header_style="bold cyan")
    for c in cols:
        table.add_column(c)
    for r in rows_d:
        table.add_row(*[_fmt(r.get(c)) for c in cols])
    console.print(table)


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    return str(v)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


@app.command()
def login() -> None:
    """僅執行登入並列出歸戶帳號。"""
    try:
        _, _, _ = _bootstrap()
    except FubonError as exc:
        console.print(f"[red]登入失敗：[/red]{exc}")
        raise typer.Exit(code=1) from exc
    client = FubonClient.instance()
    table = Table("index", "branch", "account", "name", "type", title="歸戶帳號")
    for i, a in enumerate(client.accounts):
        table.add_row(str(i), a.branch_no, a.account, a.account_name, a.account_type)
    console.print(table)
    if client.cert_info:
        console.print(
            f"[cyan]憑證：[/cyan]{client.cert_info.subject} "
            f"剩餘 {client.cert_info.days_left} 天（{client.cert_info.not_after.date()}）"
        )


@app.command()
def inventories(
    account: str | None = typer.Option(None, "--account", help="分公司-帳號，如 6460-1234567"),
    output: str = typer.Option("table", "--output", "-o", help="table | json | csv"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """庫存。"""
    _, svc, _ = _bootstrap(account)
    data = svc.inventories(force=no_cache)
    _print(data, output, "inventories")


@app.command()
def unrealized(
    account: str | None = typer.Option(None, "--account"),
    output: str = typer.Option("table", "--output", "-o"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """未實現損益。"""
    _, svc, _ = _bootstrap(account)
    data = svc.unrealized(force=no_cache)
    _print(data, output, "unrealized")


@app.command()
def realized(
    from_: str = typer.Option(
        None, "--from", "-f", help="起始日期 YYYY-MM-DD（預設：今日-30 天）"
    ),
    to: str = typer.Option(None, "--to", "-t", help="結束日期 YYYY-MM-DD（預設：今日）"),
    account: str | None = typer.Option(None, "--account"),
    output: str = typer.Option("table", "--output", "-o"),
) -> None:
    """已實現損益（不快取）。"""
    end = date.fromisoformat(to) if to else date.today()
    start = date.fromisoformat(from_) if from_ else end - timedelta(days=30)
    _, svc, _ = _bootstrap(account)
    data = svc.realized(start, end)
    _print(data, output, f"realized_{start}_{end}")


@app.command("buying-power")
def buying_power(
    account: str | None = typer.Option(None, "--account"),
    output: str = typer.Option("table", "--output", "-o"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """買進力。"""
    _, svc, _ = _bootstrap(account)
    data = svc.buying_power(force=no_cache)
    _print(data, output, "buying_power")


@app.command()
def settlements(
    account: str | None = typer.Option(None, "--account"),
    output: str = typer.Option("table", "--output", "-o"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """交割款。"""
    _, svc, _ = _bootstrap(account)
    data = svc.settlements(force=no_cache)
    _print(data, output, "settlements")


@app.command()
def maintenance(
    account: str | None = typer.Option(None, "--account"),
    output: str = typer.Option("table", "--output", "-o"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """維持率（無信用戶時回空）。"""
    _, svc, _ = _bootstrap(account)
    data = svc.maintenance(force=no_cache)
    if data is None:
        console.print("[yellow]此帳號無信用戶或未開通融資融券[/yellow]")
        return
    _print(data, output, "maintenance")


def main() -> None:  # pragma: no cover
    try:
        app()
    except FubonError as exc:
        console.print(f"[red]錯誤：[/red]{exc}")
        sys.exit(1)


if __name__ == "__main__":  # pragma: no cover
    main()
