"""Auth router：登入 / 帳號管理。

Endpoints
---------
POST   /auth/login            — 以 .env 設定執行 SDK 登入
GET    /auth/status           — 查詢目前登入狀態
GET    /auth/accounts         — 取得所有歸戶帳號
PUT    /auth/account          — 切換使用中帳號
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from stock_order_api.api.deps import ClientDep, LoginDep, TokenDep
from stock_order_api.fubon.errors import FubonLoginError

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class AccountOut(BaseModel):
    account: str
    branch_no: str
    account_type: str
    account_name: str
    display: str


class LoginOut(BaseModel):
    accounts: list[AccountOut]
    selected: AccountOut


class StatusOut(BaseModel):
    logged_in: bool
    selected: AccountOut | None


class SelectAccountIn(BaseModel):
    branch_no: str
    account_no: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _acc_out(acc: object) -> AccountOut:
    return AccountOut(
        account=getattr(acc, "account", ""),
        branch_no=getattr(acc, "branch_no", ""),
        account_type=getattr(acc, "account_type", ""),
        account_name=getattr(acc, "account_name", ""),
        display=getattr(acc, "display", ""),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/login", response_model=LoginOut)
def login(_: TokenDep, client: ClientDep) -> LoginOut:
    """使用 .env 中的憑證執行 SDK 登入，回傳歸戶帳號列表。"""
    try:
        accounts = client.login()
    except FubonLoginError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return LoginOut(
        accounts=[_acc_out(a) for a in accounts],
        selected=_acc_out(client.account),
    )


@router.get("/status", response_model=StatusOut)
def status(_: TokenDep, client: ClientDep) -> StatusOut:
    """查詢目前登入狀態。"""
    selected = _acc_out(client.account) if client.is_logged_in else None
    return StatusOut(logged_in=client.is_logged_in, selected=selected)


@router.get("/accounts", response_model=list[AccountOut])
def list_accounts(_: TokenDep, client: LoginDep) -> list[AccountOut]:
    """取得所有歸戶帳號。"""
    return [_acc_out(a) for a in client.accounts]


@router.put("/account", response_model=AccountOut)
def select_account(_: TokenDep, body: SelectAccountIn, client: LoginDep) -> AccountOut:
    """切換目前使用的帳號。"""
    try:
        acc = client.select_account(body.branch_no, body.account_no)
    except FubonLoginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _acc_out(acc)
