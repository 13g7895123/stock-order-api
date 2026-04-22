"""Account router：帳務查詢（對應 GUI 庫存/損益/現金/維持率各 Tab）。

Endpoints
---------
GET  /account/inventories        — 庫存
GET  /account/unrealized         — 未實現損益
GET  /account/realized           — 已實現損益（?from=YYYY-MM-DD&to=YYYY-MM-DD）
GET  /account/cash               — 現金 / 交割
GET  /account/maintenance        — 融資融券維持率
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query

from stock_order_api.api.deps import SvcDep, TokenDep
from stock_order_api.fubon.errors import FubonAccountError, FubonError
from stock_order_api.fubon.stock_account import (
    BuyingPower,
    InventoryItem,
    Maintenance,
    RealizedItem,
    SettlementItem,
    UnrealizedItem,
)

router = APIRouter(prefix="/account", tags=["account"])


def _wrap(fn):  # type: ignore[no-untyped-def]
    """把業務層例外統一轉為 HTTP 502。"""
    try:
        return fn()
    except (FubonError, FubonAccountError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/inventories", response_model=list[InventoryItem])
def inventories(
    _: TokenDep,
    svc: SvcDep,
    force: bool = Query(False, description="忽略快取強制重查"),
) -> list[InventoryItem]:
    """查詢庫存。"""
    return _wrap(lambda: svc.inventories(force=force))


@router.get("/unrealized", response_model=list[UnrealizedItem])
def unrealized(
    _: TokenDep,
    svc: SvcDep,
    force: bool = Query(False, description="忽略快取強制重查"),
) -> list[UnrealizedItem]:
    """查詢未實現損益。"""
    return _wrap(lambda: svc.unrealized(force=force))


@router.get("/realized", response_model=list[RealizedItem])
def realized(
    _: TokenDep,
    svc: SvcDep,
    from_date: date = Query(alias="from", description="起始日 YYYY-MM-DD"),
    to_date: date = Query(alias="to", description="結束日 YYYY-MM-DD"),
) -> list[RealizedItem]:
    """查詢已實現損益（最長 90 天，超過自動切片）。"""
    if to_date < from_date:
        raise HTTPException(status_code=422, detail="to 不可早於 from")
    return _wrap(lambda: svc.realized(from_date, to_date))


@router.get("/cash", response_model=BuyingPower)
def cash(
    _: TokenDep,
    svc: SvcDep,
    force: bool = Query(False, description="忽略快取強制重查"),
) -> BuyingPower:
    """查詢現金 / 交割餘額與買進力。"""
    return _wrap(lambda: svc.buying_power(force=force))


@router.get("/settlements", response_model=list[SettlementItem])
def settlements(
    _: TokenDep,
    svc: SvcDep,
    force: bool = Query(False, description="忽略快取強制重查"),
) -> list[SettlementItem]:
    """查詢交割款。"""
    return _wrap(lambda: svc.settlements(force=force))


@router.get("/maintenance", response_model=Maintenance | None)
def maintenance(
    _: TokenDep,
    svc: SvcDep,
    force: bool = Query(False, description="忽略快取強制重查"),
) -> Maintenance | None:
    """查詢融資融券維持率。"""
    return _wrap(lambda: svc.maintenance(force=force))
