"""股票帳務查詢封裝。

依 [plan-account.md](../../../plan-account.md) §5-§7 實作：
- 六個查詢方法（庫存/未實現/已實現/買進力/交割/維持率）
- Pydantic DTO
- TTL cache
- 稽核寫入 & snapshot
- 以 `logger.debug(repr(raw))` 對照 SDK 實際欄位（Step 4 策略）
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from loguru import logger
from pydantic import BaseModel, ConfigDict

from stock_order_api.audit.store import AuditStore
from stock_order_api.fubon.client import AccountRef, FubonClient
from stock_order_api.fubon.errors import FubonAccountError
from stock_order_api.utils.cache import TTLCache

# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class InventoryItem(_DTO):
    account: str
    symbol: str
    name: str | None = None
    order_type: str = ""
    today_qty: int = 0
    total_qty: int = 0
    avg_price: Decimal = Decimal("0")
    market_value: Decimal | None = None


class UnrealizedItem(_DTO):
    account: str
    symbol: str
    order_type: str = ""
    qty: int = 0
    avg_price: Decimal = Decimal("0")
    last_price: Decimal | None = None
    pnl: Decimal = Decimal("0")
    pnl_rate: Decimal | None = None


class RealizedItem(_DTO):
    account: str
    trade_date: date
    symbol: str
    order_type: str = ""
    qty: int = 0
    buy_price: Decimal = Decimal("0")
    sell_price: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")
    fee: Decimal | None = None
    tax: Decimal | None = None


class BuyingPower(_DTO):
    account: str
    cash: Decimal = Decimal("0")
    buying_power: Decimal = Decimal("0")
    margin_quota: Decimal | None = None
    short_quota: Decimal | None = None


class SettlementItem(_DTO):
    account: str
    t_date: date
    amount: Decimal = Decimal("0")


class Maintenance(_DTO):
    account: str
    maintenance_rate: Decimal = Decimal("0")
    margin_value: Decimal = Decimal("0")
    short_value: Decimal = Decimal("0")
    warning_line: Decimal | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _d(v: Any, default: str = "0") -> Decimal:
    if v is None or v == "":
        return Decimal(default)
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _i(v: Any, default: int = 0) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _get(raw: Any, *names: str, default: Any = None) -> Any:
    """容錯取欄位：兼顧 dict 與物件 attribute，並嘗試多個別名。"""
    for n in names:
        if isinstance(raw, dict):
            if n in raw:
                return raw[n]
        else:
            v = getattr(raw, n, None)
            if v is not None:
                return v
    return default


def _to_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    if v is None:
        return date.today()
    s = str(v)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            from datetime import datetime

            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()


def _raw_repr(raw: Any) -> str:
    """安全地把 SDK 物件轉為可讀字串，供 DEBUG log。"""
    try:
        return repr(raw)
    except Exception:
        return f"<unrepr-able {type(raw).__name__}>"


def _unwrap_result(result: Any, event: str) -> Any:
    """檢查 `Result{is_success, message, data}`；失敗時 raise。"""
    if not getattr(result, "is_success", False):
        msg = getattr(result, "message", "unknown")
        code = getattr(result, "code", None)
        raise FubonAccountError(f"{event} 失敗：{msg}", code=code)
    return getattr(result, "data", None)


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------


def _account_key(acc: AccountRef) -> str:
    return f"{acc.branch_no}-{acc.account}"


def map_inventory(raw: Any, acc: AccountRef) -> InventoryItem:
    return InventoryItem(
        account=_account_key(acc),
        symbol=str(_get(raw, "stock_no", "symbol", "stock_symbol", default="")),
        name=_get(raw, "stock_name", "name"),
        order_type=str(_get(raw, "order_type", "type", default="")),
        today_qty=_i(_get(raw, "today_qty", "tradable_qty", "today_sellable_qty")),
        total_qty=_i(_get(raw, "total_qty", "stock_qty", "qty")),
        avg_price=_d(_get(raw, "avg_price", "cost_price")),
        market_value=_get(raw, "market_value"),
    )


def map_unrealized(raw: Any, acc: AccountRef) -> UnrealizedItem:
    return UnrealizedItem(
        account=_account_key(acc),
        symbol=str(_get(raw, "stock_no", "symbol", default="")),
        order_type=str(_get(raw, "order_type", default="")),
        qty=_i(_get(raw, "qty", "stock_qty", "today_qty")),
        avg_price=_d(_get(raw, "avg_price", "cost_price")),
        last_price=_get(raw, "last_price", "close_price", "price"),
        pnl=_d(_get(raw, "pnl", "unrealized_profit", "unrealized_pnl")),
        pnl_rate=_get(raw, "pnl_rate", "unrealized_profit_rate"),
    )


def map_realized(raw: Any, acc: AccountRef) -> RealizedItem:
    return RealizedItem(
        account=_account_key(acc),
        trade_date=_to_date(_get(raw, "trade_date", "date", "sell_date")),
        symbol=str(_get(raw, "stock_no", "symbol", default="")),
        order_type=str(_get(raw, "order_type", default="")),
        qty=_i(_get(raw, "qty", "stock_qty")),
        buy_price=_d(_get(raw, "buy_price", "cost_price")),
        sell_price=_d(_get(raw, "sell_price", "price")),
        pnl=_d(_get(raw, "pnl", "realized_profit", "profit")),
        fee=_get(raw, "fee"),
        tax=_get(raw, "tax"),
    )


def map_buying_power(raw: Any, acc: AccountRef) -> BuyingPower:
    return BuyingPower(
        account=_account_key(acc),
        cash=_d(_get(raw, "cash", "cash_balance")),
        buying_power=_d(_get(raw, "buying_power", "bp", "available")),
        margin_quota=_get(raw, "margin_quota"),
        short_quota=_get(raw, "short_quota"),
    )


def map_settlement(raw: Any, acc: AccountRef) -> SettlementItem:
    return SettlementItem(
        account=_account_key(acc),
        t_date=_to_date(_get(raw, "t_date", "settle_date", "date")),
        amount=_d(_get(raw, "amount", "settlement_amount")),
    )


def map_maintenance(raw: Any, acc: AccountRef) -> Maintenance:
    return Maintenance(
        account=_account_key(acc),
        maintenance_rate=_d(_get(raw, "maintenance_rate", "rate")),
        margin_value=_d(_get(raw, "margin_value")),
        short_value=_d(_get(raw, "short_value")),
        warning_line=_get(raw, "warning_line"),
    )


# ---------------------------------------------------------------------------
# Main facade
# ---------------------------------------------------------------------------


class StockAccount:
    """封裝富邦帳務查詢。

    初始化需傳入已登入的 `FubonClient` 與 `AuditStore`。
    快取 TTL 規則（與 plan-account.md §6 對齊）：
      * inventories / unrealized  → 10 秒
      * buying_power / settlements / maintenance → 30 秒
      * realized → 不快取
    """

    INVENTORY_TTL = 10
    UNREALIZED_TTL = 10
    CASH_TTL = 30

    def __init__(self, client: FubonClient, audit: AuditStore | None = None) -> None:
        self.client = client
        self.audit = audit
        self.cache = TTLCache(store=audit)

    # ---- 六個查詢 ----
    def inventories(self, *, force: bool = False) -> list[InventoryItem]:
        return self._query_list(
            event="QUERY_INVENTORY",
            kind="inventories",
            ttl=self.INVENTORY_TTL,
            sdk_call=lambda acc: self.client.sdk.stock.inventories(acc.raw),
            mapper=map_inventory,
            force=force,
        )

    def unrealized(self, *, force: bool = False) -> list[UnrealizedItem]:
        return self._query_list(
            event="QUERY_UNREALIZED",
            kind="unrealized",
            ttl=self.UNREALIZED_TTL,
            sdk_call=lambda acc: self.client.sdk.stock.unrealized_gains_and_loses(acc.raw),
            mapper=map_unrealized,
            force=force,
        )

    def realized(self, start: date, end: date) -> list[RealizedItem]:
        """查詢已實現損益；超過 90 天自動切片。"""
        if end < start:
            raise ValueError("end 早於 start")
        chunks = list(_chunk_date_range(start, end, 90))
        out: list[RealizedItem] = []
        for s, e in chunks:
            out.extend(self._realized_chunk(s, e))
        out.sort(key=lambda x: (x.trade_date, x.symbol))
        return out

    def _realized_chunk(self, start: date, end: date) -> list[RealizedItem]:
        return self._query_list(
            event="QUERY_REALIZED",
            kind=f"realized_{start.isoformat()}_{end.isoformat()}",
            ttl=0,  # 不快取
            sdk_call=lambda acc: self.client.sdk.stock.realized_gains_and_loses(
                acc.raw, start.isoformat(), end.isoformat()
            ),
            mapper=map_realized,
            force=True,
            extra={"from": start.isoformat(), "to": end.isoformat()},
        )

    def buying_power(self, *, force: bool = False) -> BuyingPower:
        return cast(
            BuyingPower,
            self._query_single(
                event="QUERY_BUYING_POWER",
                kind="buying_power",
                ttl=self.CASH_TTL,
                sdk_call=lambda acc: self.client.sdk.stock.buying_power(acc.raw),
                mapper=map_buying_power,
                force=force,
            ),
        )

    def settlements(self, *, force: bool = False) -> list[SettlementItem]:
        def _call(acc: AccountRef) -> Any:
            # SDK 可能需 `range` 參數；先試不帶、再試帶
            fn = self.client.sdk.stock.settlements
            try:
                return fn(acc.raw)
            except TypeError:
                return fn(acc.raw, "0d")  # pragma: no cover

        return self._query_list(
            event="QUERY_SETTLEMENTS",
            kind="settlements",
            ttl=self.CASH_TTL,
            sdk_call=_call,
            mapper=map_settlement,
            force=force,
        )

    def maintenance(self, *, force: bool = False) -> Maintenance | None:
        try:
            result = self._query_single(
                event="QUERY_MAINTENANCE",
                kind="maintenance",
                ttl=self.CASH_TTL,
                sdk_call=lambda acc: self.client.sdk.stock.maintenance(acc.raw),
                mapper=map_maintenance,
                force=force,
            )
            return cast(Maintenance, result)
        except FubonAccountError as exc:
            # 無信用戶時 SDK 可能回錯；降級回 None
            logger.bind(event="QUERY_MAINTENANCE").warning(f"maintenance 不可用：{exc}")
            return None

    # ---- 內部共用 ----
    def _query_list(
        self,
        *,
        event: str,
        kind: str,
        ttl: int,
        sdk_call: Any,
        mapper: Any,
        force: bool,
        extra: dict[str, Any] | None = None,
    ) -> list[Any]:
        def loader() -> list[dict[str, Any]]:
            rows = self._invoke_sdk(event, sdk_call)
            return [mapper(r, self.client.account).model_dump(mode="json") for r in rows]

        cache_key = f"{kind}:{_account_key(self.client.account)}"
        if ttl <= 0:
            payload = loader()
            source = "api"
        else:
            payload, source = self.cache.get_or_fetch(cache_key, ttl, loader, force_refresh=force)
        self._audit_ok(event, source, kind, payload, extra)
        model_cls = _infer_model(mapper)
        return [model_cls.model_validate(d) for d in payload]

    def _query_single(
        self,
        *,
        event: str,
        kind: str,
        ttl: int,
        sdk_call: Any,
        mapper: Any,
        force: bool,
    ) -> Any:
        def loader() -> dict[str, Any]:
            data = self._invoke_sdk(event, sdk_call, expect_list=False)
            raw = data[0] if isinstance(data, list) and data else data
            return cast(dict[str, Any], mapper(raw, self.client.account).model_dump(mode="json"))

        cache_key = f"{kind}:{_account_key(self.client.account)}"
        payload, source = self.cache.get_or_fetch(cache_key, ttl, loader, force_refresh=force)
        self._audit_ok(event, source, kind, payload, None)
        model_cls = _infer_model(mapper)
        return model_cls.model_validate(payload)

    def _invoke_sdk(self, event: str, sdk_call: Any, *, expect_list: bool = True) -> Any:
        req_id = uuid.uuid4().hex
        log = logger.bind(event=event, request_id=req_id, account=_account_key(self.client.account))
        t0 = time.perf_counter()
        log.debug("SDK call start")
        try:
            result = sdk_call(self.client.account)
        except Exception as exc:
            log.exception(f"SDK raised: {exc}")
            self._audit_err(event, req_id, str(exc))
            raise FubonAccountError(f"{event} SDK 拋錯：{exc}") from exc

        elapsed = int((time.perf_counter() - t0) * 1000)
        log = log.bind(elapsed_ms=elapsed)
        try:
            data = _unwrap_result(result, event)
        except FubonAccountError as exc:
            log.error(f"failed: {exc}")
            self._audit_err(event, req_id, str(exc))
            raise

        if expect_list:
            data = data or []
            if not isinstance(data, (list, tuple)):
                data = [data]
            count = len(data)
        else:
            count = 0 if data is None else 1

        log.debug(f"SDK raw sample: {_raw_repr(data[:2] if expect_list else data)}")
        log.info(f"ok count={count}")
        return data

    def _audit_ok(
        self,
        event: str,
        source: str,
        kind: str,
        payload: Any,
        extra: dict[str, Any] | None,
    ) -> None:
        if self.audit is None:
            return
        acc = _account_key(self.client.account)
        msg = f"source={source} kind={kind}"
        if extra:
            msg += " " + " ".join(f"{k}={v}" for k, v in extra.items())
        self.audit.log_event(event, ok=True, account=acc, message=msg, payload=None)
        if source == "api":
            self.audit.save_snapshot(kind=kind, account=acc, payload=payload)
            logger.bind(event="SNAPSHOT_WRITTEN", account=acc).debug(f"kind={kind}")

    def _audit_err(self, event: str, request_id: str, message: str) -> None:
        if self.audit is None:
            return
        acc = _account_key(self.client.account) if self.client.is_logged_in else None
        self.audit.log_event(
            event, ok=False, request_id=request_id, account=acc, message=message
        )


# ---------------------------------------------------------------------------


_MODEL_CACHE: dict[Any, type[BaseModel]] = {
    map_inventory: InventoryItem,
    map_unrealized: UnrealizedItem,
    map_realized: RealizedItem,
    map_buying_power: BuyingPower,
    map_settlement: SettlementItem,
    map_maintenance: Maintenance,
}


def _infer_model(mapper: Any) -> type[BaseModel]:
    if mapper in _MODEL_CACHE:
        return _MODEL_CACHE[mapper]
    raise RuntimeError(f"未註冊的 mapper：{mapper}")


def _chunk_date_range(start: date, end: date, days: int) -> Iterable[tuple[date, date]]:
    cur = start
    step = timedelta(days=days - 1)
    while cur <= end:
        nxt = min(cur + step, end)
        yield cur, nxt
        cur = nxt + timedelta(days=1)


__all__ = [
    "StockAccount",
    "InventoryItem",
    "UnrealizedItem",
    "RealizedItem",
    "BuyingPower",
    "SettlementItem",
    "Maintenance",
    "map_inventory",
    "map_unrealized",
    "map_realized",
    "map_buying_power",
    "map_settlement",
    "map_maintenance",
]
