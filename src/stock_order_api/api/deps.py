"""FastAPI 共用 Depends。

所有 router 透過此模組取得服務物件，不直接建立實例。
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from stock_order_api.audit.store import AuditStore
from stock_order_api.config import get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.fubon.errors import FubonError
from stock_order_api.fubon.stock_account import StockAccount
from stock_order_api.fubon.stock_order import StockOrderService


# ---------------------------------------------------------------------------
# 可選 Token 保護
# ---------------------------------------------------------------------------
# 若設定環境變數 STOCK_SERVER_TOKEN，所有請求必須帶 X-API-Key header。


def verify_token(x_api_key: Annotated[str | None, Header()] = None) -> None:
    token = os.environ.get("STOCK_SERVER_TOKEN")
    if token and x_api_key != token:
        raise HTTPException(status_code=401, detail="X-API-Key 無效或缺失")


TokenDep = Annotated[None, Depends(verify_token)]


# ---------------------------------------------------------------------------
# AuditStore（儲存於 app.state，由 lifespan 注入）
# ---------------------------------------------------------------------------


def get_audit(request: Request) -> AuditStore:
    return request.app.state.audit  # type: ignore[no-any-return]


AuditDep = Annotated[AuditStore, Depends(get_audit)]


# ---------------------------------------------------------------------------
# FubonClient
# ---------------------------------------------------------------------------


def get_client() -> FubonClient:
    return FubonClient.instance(get_settings())


ClientDep = Annotated[FubonClient, Depends(get_client)]


def require_login(client: ClientDep) -> FubonClient:
    if not client.is_logged_in:
        raise HTTPException(status_code=401, detail="尚未登入，請先呼叫 POST /auth/login")
    return client


LoginDep = Annotated[FubonClient, Depends(require_login)]


# ---------------------------------------------------------------------------
# 業務服務
# ---------------------------------------------------------------------------


def get_svc(
    client: LoginDep,
    audit: AuditDep,
) -> StockAccount:
    return StockAccount(client, audit)


SvcDep = Annotated[StockAccount, Depends(get_svc)]


def get_order_svc(client: LoginDep) -> StockOrderService:
    return StockOrderService(client)


OrderSvcDep = Annotated[StockOrderService, Depends(get_order_svc)]


# ---------------------------------------------------------------------------
# 通用錯誤轉換（業務層 exception → HTTP）
# ---------------------------------------------------------------------------


def handle_fubon_error(exc: FubonError) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))
