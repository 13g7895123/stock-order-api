"""Orders router：委託查詢 / 下單 / 改價 / 改量 / 刪單。

Endpoints
---------
GET    /orders                    — 委託列表
POST   /orders                    — 下單
DELETE /orders/{order_no}         — 刪單
PATCH  /orders/{order_no}/price   — 改價
PATCH  /orders/{order_no}/quantity— 改量
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from stock_order_api.api.deps import OrderSvcDep, TokenDep
from stock_order_api.fubon.errors import FubonError
from stock_order_api.fubon.stock_order import (
    ORDER_TYPE_CHOICES,
    MARKET_TYPE_CHOICES,
    PRICE_TYPE_CHOICES,
    SIDE_CHOICES,
    TIF_CHOICES,
    OrderRecord,
    OrderRequest,
    OrderResult,
)

router = APIRouter(prefix="/orders", tags=["orders"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class PlaceOrderIn(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    side: str = Field(..., description=f"買賣方向：{SIDE_CHOICES}")
    quantity: int = Field(..., gt=0)
    price: Decimal | None = Field(None, description="限價單填入；市價可為 null")
    price_type: str = Field("Limit", description=f"價別：{PRICE_TYPE_CHOICES}")
    time_in_force: str = Field("ROD", description=f"TIF：{TIF_CHOICES}")
    market_type: str = Field("Common", description=f"盤別：{MARKET_TYPE_CHOICES}")
    order_type: str = Field("Stock", description=f"委託類別：{ORDER_TYPE_CHOICES}")
    user_def: str | None = None


class ModifyPriceIn(BaseModel):
    price: Decimal = Field(..., description="新委託價格")


class ModifyQtyIn(BaseModel):
    quantity: int = Field(..., gt=0, description="新委託股數")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _find_raw(svc: object, order_no: str) -> object:
    """重新查詢委託單找到對應的 raw SDK 物件；找不到 raise 404。"""
    try:
        records = svc.list_orders()  # type: ignore[attr-defined]
    except FubonError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    for rec, raw in records:
        if rec.order_no == order_no:
            return raw

    raise HTTPException(status_code=404, detail=f"找不到委託單 {order_no}")


def _exec(fn):  # type: ignore[no-untyped-def]
    try:
        return fn()
    except FubonError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[OrderRecord])
def list_orders(_: TokenDep, svc: OrderSvcDep) -> list[OrderRecord]:
    """取得目前帳號的委託列表。"""
    return _exec(lambda: [rec for rec, _ in svc.list_orders()])


@router.post("", response_model=OrderResult, status_code=201)
def place_order(_: TokenDep, body: PlaceOrderIn, svc: OrderSvcDep) -> OrderResult:
    """送出新委託。"""
    req = OrderRequest(
        symbol=body.symbol,
        side=body.side,
        quantity=body.quantity,
        price=body.price,
        price_type=body.price_type,
        time_in_force=body.time_in_force,
        market_type=body.market_type,
        order_type=body.order_type,
        user_def=body.user_def,
    )
    try:
        return svc.place(req)
    except (FubonError, ValueError) as exc:
        raise HTTPException(status_code=422 if isinstance(exc, ValueError) else 502, detail=str(exc)) from exc


@router.delete("/{order_no}", response_model=OrderResult)
def cancel_order(_: TokenDep, order_no: str, svc: OrderSvcDep) -> OrderResult:
    """刪除指定委託單。"""
    raw = _find_raw(svc, order_no)
    return _exec(lambda: svc.cancel(raw))


@router.patch("/{order_no}/price", response_model=OrderResult)
def modify_price(
    _: TokenDep, order_no: str, body: ModifyPriceIn, svc: OrderSvcDep
) -> OrderResult:
    """改價。"""
    raw = _find_raw(svc, order_no)
    return _exec(lambda: svc.modify_price(raw, body.price))


@router.patch("/{order_no}/quantity", response_model=OrderResult)
def modify_quantity(
    _: TokenDep, order_no: str, body: ModifyQtyIn, svc: OrderSvcDep
) -> OrderResult:
    """改量。"""
    raw = _find_raw(svc, order_no)
    return _exec(lambda: svc.modify_quantity(raw, body.quantity))
