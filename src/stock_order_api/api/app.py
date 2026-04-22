"""FastAPI 應用程式建立與 lifespan。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from stock_order_api.audit.store import AuditStore
from stock_order_api.config import get_settings
from stock_order_api.fubon.errors import FubonError
from stock_order_api.logging_setup import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    setup_logging(log_dir=s.log_dir, level="INFO")
    app.state.audit = AuditStore(s.audit_db_path)
    yield
    # 目前無需額外清理


def create_app() -> FastAPI:
    app = FastAPI(
        title="Stock Order API",
        description="富邦 Neo 帳務 + 下單 + 即時行情 REST/WS API",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---------------------------------------------------------------------------
    # Routers
    # ---------------------------------------------------------------------------
    from stock_order_api.api.routers.account import router as account_router
    from stock_order_api.api.routers.auth import router as auth_router
    from stock_order_api.api.routers.orders import router as orders_router
    from stock_order_api.api.routers.realtime import router as realtime_router

    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(orders_router)
    app.include_router(realtime_router)

    # ---------------------------------------------------------------------------
    # 全域例外處理
    # ---------------------------------------------------------------------------
    @app.exception_handler(FubonError)
    async def fubon_error_handler(request: Request, exc: FubonError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/healthz", tags=["system"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
