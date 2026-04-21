"""股票下單封裝（place / modify / cancel / query）。

此模組負責把易於使用的 Python 值轉為 `fubon_neo.sdk.Order` 並呼叫
`sdk.stock.place_order(acc, order)`。所有呼叫皆會：

1. 先做參數檢查（symbol / qty / price）
2. 記 `loguru` 事件（`ORDER_SUBMIT` / `ORDER_RESULT` / `ORDER_ERROR`）
3. 若 `settings.dry_run=True`，僅 log 不實際送出
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict

from stock_order_api.fubon.client import AccountRef, FubonClient
from stock_order_api.fubon.errors import FubonError

# ---------------------------------------------------------------------------
# Public enums (字串化，便於 GUI / CLI 使用)
# ---------------------------------------------------------------------------

SIDE_CHOICES = ("Buy", "Sell")
PRICE_TYPE_CHOICES = ("Limit", "Market", "LimitUp", "LimitDown", "Reference")
TIF_CHOICES = ("ROD", "IOC", "FOK")
MARKET_TYPE_CHOICES = ("Common", "Odd", "IntradayOdd", "Fixing", "Emg", "EmgOdd")
ORDER_TYPE_CHOICES = ("Stock", "Margin", "Short", "DayTrade", "SBL")


# ---------------------------------------------------------------------------
# DTO
# ---------------------------------------------------------------------------


class _DTO(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


@dataclass
class OrderRequest:
    """使用者填入的下單參數（皆為字串/原生型別）。"""

    symbol: str
    side: str  # Buy/Sell
    quantity: int
    price: str | Decimal | None = None  # 市價可為 None
    price_type: str = "Limit"
    time_in_force: str = "ROD"
    market_type: str = "Common"
    order_type: str = "Stock"
    user_def: str | None = None


class OrderResult(_DTO):
    """下單回傳（擷取常用欄位）。"""

    is_success: bool
    message: str | None = None
    order_no: str | None = None
    seq_no: str | None = None
    symbol: str = ""
    side: str = ""
    quantity: int = 0
    price: str | None = None
    status: str | None = None
    raw_repr: str = ""


class OrderRecord(_DTO):
    """委託回報單筆（用於委託查詢 / GUI 表格）。"""

    order_no: str = ""
    seq_no: str = ""
    symbol: str = ""
    side: str = ""
    price: str = ""
    quantity: int = 0
    filled_qty: int = 0
    remain_qty: int = 0
    status: str = ""
    price_type: str = ""
    time_in_force: str = ""
    market_type: str = ""
    order_type: str = ""
    last_time: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum(mod_name: str, value: str) -> Any:
    """把使用者傳入的字串轉為 `fubon_neo.constant.<Enum>.<value>`。"""
    from fubon_neo import constant as C

    enum_cls = getattr(C, mod_name)
    try:
        return getattr(enum_cls, value)
    except AttributeError as exc:
        raise ValueError(f"{mod_name} 不支援 {value!r}") from exc


def _build_order(req: OrderRequest) -> Any:
    from fubon_neo.sdk import Order

    if not req.symbol:
        raise ValueError("symbol 不可為空")
    if req.quantity <= 0:
        raise ValueError("quantity 必須 > 0")
    price_type = _enum("PriceType", req.price_type)
    price_str = None if req.price is None else str(req.price)
    # 市價單 price 必須為 None；限價單必須有價
    if req.price_type == "Limit" and not price_str:
        raise ValueError("限價單必須提供 price")

    return Order(
        buy_sell=_enum("BSAction", req.side),
        symbol=req.symbol,
        price=price_str,
        quantity=int(req.quantity),
        market_type=_enum("MarketType", req.market_type),
        price_type=price_type,
        time_in_force=_enum("TimeInForce", req.time_in_force),
        order_type=_enum("OrderType", req.order_type),
        user_def=req.user_def,
    )


def _safe_repr(obj: Any) -> str:
    try:
        return repr(obj)
    except Exception:
        return f"<unrepr {type(obj).__name__}>"


def _get(raw: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(raw, dict):
            if n in raw:
                return raw[n]
        else:
            v = getattr(raw, n, None)
            if v is not None:
                return v
    return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _map_order_record(raw: Any) -> OrderRecord:
    return OrderRecord(
        order_no=str(_get(raw, "order_no", default="")),
        seq_no=str(_get(raw, "seq_no", default="")),
        symbol=str(_get(raw, "stock_no", "symbol", default="")),
        side=str(_get(raw, "buy_sell", default="")),
        price=str(_get(raw, "price", default="") or ""),
        quantity=_i(_get(raw, "quantity", "order_quantity")),
        filled_qty=_i(_get(raw, "filled_qty", "filled_quantity", "after_qty")),
        remain_qty=_i(_get(raw, "remain_qty", "leaves_qty")),
        status=str(_get(raw, "status", default="")),
        price_type=str(_get(raw, "price_type", default="")),
        time_in_force=str(_get(raw, "time_in_force", default="")),
        market_type=str(_get(raw, "market_type", default="")),
        order_type=str(_get(raw, "order_type", default="")),
        last_time=str(_get(raw, "last_time", "order_time", default="")),
    )


# ---------------------------------------------------------------------------
# Facade
# ---------------------------------------------------------------------------


class StockOrderService:
    """股票下單服務。"""

    def __init__(self, client: FubonClient) -> None:
        self.client = client

    # ---- 帳號 ----
    def _acc(self) -> AccountRef:
        acc = self.client.account
        if acc is None:
            raise FubonError("尚未選擇帳號；請先 login()")
        return acc

    # ---- 下單 ----
    def place(self, req: OrderRequest) -> OrderResult:
        acc = self._acc()
        order_obj = _build_order(req)
        log = logger.bind(
            event="ORDER_SUBMIT",
            symbol=req.symbol,
            side=req.side,
            qty=req.quantity,
            price=str(req.price) if req.price is not None else None,
            price_type=req.price_type,
            tif=req.time_in_force,
            market=req.market_type,
            order_type=req.order_type,
        )
        log.info("submit")

        if self.client.settings.dry_run:
            log.warning("dry_run=True；不送出")
            return OrderResult(
                is_success=True,
                message="DRY_RUN",
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                price=str(req.price) if req.price is not None else None,
                status="DRY_RUN",
                raw_repr="dry_run",
            )

        try:
            result = self.client.sdk.stock.place_order(acc.raw, order_obj)
        except Exception as exc:
            logger.bind(event="ORDER_ERROR").exception(f"place_order failed: {exc}")
            raise FubonError(f"下單失敗：{exc}") from exc

        ok = bool(getattr(result, "is_success", False))
        msg = getattr(result, "message", None)
        data = getattr(result, "data", None)
        order_no = str(_get(data, "order_no", default="") or "")
        seq_no = str(_get(data, "seq_no", default="") or "")
        status = str(_get(data, "status", default="") or "")
        logger.bind(
            event="ORDER_RESULT",
            ok=ok,
            order_no=order_no,
            seq_no=seq_no,
            status=status,
            message=msg,
        ).info("result")

        return OrderResult(
            is_success=ok,
            message=msg,
            order_no=order_no or None,
            seq_no=seq_no or None,
            symbol=req.symbol,
            side=req.side,
            quantity=req.quantity,
            price=str(req.price) if req.price is not None else None,
            status=status or None,
            raw_repr=_safe_repr(result),
        )

    # ---- 改量 / 改價 / 刪單 ----
    def cancel(self, order_record: Any) -> OrderResult:
        acc = self._acc()
        logger.bind(event="ORDER_CANCEL").info(
            f"cancel {getattr(order_record, 'order_no', order_record)}"
        )
        if self.client.settings.dry_run:
            return OrderResult(is_success=True, message="DRY_RUN", status="DRY_RUN")
        try:
            result = self.client.sdk.stock.cancel_order(acc.raw, order_record)
        except Exception as exc:
            logger.bind(event="ORDER_ERROR").exception(f"cancel_order failed: {exc}")
            raise FubonError(f"刪單失敗：{exc}") from exc
        ok = bool(getattr(result, "is_success", False))
        return OrderResult(
            is_success=ok,
            message=getattr(result, "message", None),
            status="CANCELED" if ok else None,
            raw_repr=_safe_repr(result),
        )

    def modify_price(self, order_record: Any, new_price: str | Decimal) -> OrderResult:
        acc = self._acc()
        logger.bind(event="ORDER_MODIFY_PRICE").info(f"new_price={new_price}")
        if self.client.settings.dry_run:
            return OrderResult(is_success=True, message="DRY_RUN", status="DRY_RUN")
        try:
            mod_obj = self.client.sdk.stock.make_modify_price_obj(
                order_record, str(new_price)
            )
            result = self.client.sdk.stock.modify_price(acc.raw, mod_obj)
        except Exception as exc:
            logger.bind(event="ORDER_ERROR").exception(f"modify_price failed: {exc}")
            raise FubonError(f"改價失敗：{exc}") from exc
        ok = bool(getattr(result, "is_success", False))
        return OrderResult(
            is_success=ok,
            message=getattr(result, "message", None),
            status="MODIFIED" if ok else None,
            raw_repr=_safe_repr(result),
        )

    def modify_quantity(self, order_record: Any, new_qty: int) -> OrderResult:
        acc = self._acc()
        logger.bind(event="ORDER_MODIFY_QTY").info(f"new_qty={new_qty}")
        if self.client.settings.dry_run:
            return OrderResult(is_success=True, message="DRY_RUN", status="DRY_RUN")
        try:
            mod_obj = self.client.sdk.stock.make_modify_quantity_obj(
                order_record, int(new_qty)
            )
            result = self.client.sdk.stock.modify_quantity(acc.raw, mod_obj)
        except Exception as exc:
            logger.bind(event="ORDER_ERROR").exception(f"modify_quantity failed: {exc}")
            raise FubonError(f"改量失敗：{exc}") from exc
        ok = bool(getattr(result, "is_success", False))
        return OrderResult(
            is_success=ok,
            message=getattr(result, "message", None),
            status="MODIFIED" if ok else None,
            raw_repr=_safe_repr(result),
        )

    # ---- 委託查詢 ----
    def list_orders(self) -> list[tuple[OrderRecord, Any]]:
        """回傳 [(OrderRecord, raw_sdk_object), ...]。raw 用於後續 cancel/modify。"""
        acc = self._acc()
        try:
            result = self.client.sdk.stock.get_order_results(acc.raw)
        except Exception as exc:
            logger.bind(event="ORDER_ERROR").exception(f"get_order_results: {exc}")
            raise FubonError(f"查詢委託失敗：{exc}") from exc
        if not getattr(result, "is_success", False):
            raise FubonError(
                f"查詢委託失敗：{getattr(result, 'message', 'unknown')}"
            )
        data = getattr(result, "data", None) or []
        return [(_map_order_record(r), r) for r in data]
