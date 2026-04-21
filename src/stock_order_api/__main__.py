"""`python -m stock_order_api` → 啟動 GUI。"""

from __future__ import annotations


def main() -> int:
    from stock_order_api.gui.app import run_gui

    return run_gui()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
