"""`python -m stock_order_api.api` 或 `stock-order-api` 啟動 FastAPI server。"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock Order REST API server")
    parser.add_argument("--host", default="127.0.0.1", help="綁定 IP（預設 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000, help="埠號（預設 8000）")
    parser.add_argument("--reload", action="store_true", help="開發模式：自動重載")
    parser.add_argument("--log-level", default="info", help="uvicorn log level")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "stock_order_api.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
