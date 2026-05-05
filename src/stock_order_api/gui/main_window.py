"""主視窗：狀態列 + 帳號下拉 + 五個 Tab + Log 面板。"""

from __future__ import annotations

from datetime import date
from typing import Any

from loguru import logger
from PySide6.QtCore import QDate, QObject, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from stock_order_api.audit.store import AuditStore
from stock_order_api.config import Settings, get_settings
from stock_order_api.fubon.client import FubonClient
from stock_order_api.fubon.errors import FubonError
from stock_order_api.fubon.stock_account import StockAccount
from stock_order_api.gui.login_dialog import LoginDialog
from stock_order_api.gui.pages.order_page import OrderPage
from stock_order_api.gui.pages.quote_page import QuotePage
from stock_order_api.gui.pages.table_page import TablePage
from stock_order_api.logging_setup import register_qt_sink

# ---------------------------------------------------------------------------
# Log 信號轉接（把 loguru sink 轉為 Qt signal）
# ---------------------------------------------------------------------------


class LogBridge(QObject):
    emitted = Signal(str)


class LogPanel(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Log", parent)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(5000)
        self.setWidget(self.view)
        self.bridge = LogBridge()
        self.bridge.emitted.connect(self._append, Qt.ConnectionType.QueuedConnection)
        self._sink_id = register_qt_sink(self.bridge.emitted.emit, level="INFO")

    def _append(self, msg: str) -> None:
        self.view.appendPlainText(msg.rstrip())


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("富邦 Neo - 帳務 + 即時行情 + 下單")
        self.resize(1180, 720)

        self.settings = get_settings()
        self.audit = AuditStore(self.settings.audit_db_path)
        self.client = FubonClient.instance(self.settings)
        self.svc: StockAccount | None = None

        # --- toolbar：帳號下拉
        self.tb = QToolBar("帳號")
        self.lbl_login = QLabel("未登入")
        self.cbx_account = QComboBox()
        self.cbx_account.setMinimumWidth(240)
        self.cbx_account.currentIndexChanged.connect(self._on_account_changed)
        self.tb.addWidget(QLabel("  登入狀態："))
        self.tb.addWidget(self.lbl_login)
        self.tb.addSeparator()
        self.tb.addWidget(QLabel("帳號："))
        self.tb.addWidget(self.cbx_account)
        self.addToolBar(self.tb)

        # --- menu
        act_login = QAction("登入", self)
        act_login.triggered.connect(self._do_login)
        self.menuBar().addAction(act_login)
        act_refresh_all = QAction("全部重整", self)
        act_refresh_all.triggered.connect(self._refresh_all)
        self.menuBar().addAction(act_refresh_all)

        # --- central tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self._build_tabs()

        # --- log dock
        self.log_panel = LogPanel(self)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.log_panel)

        # --- statusbar
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.sb_cert = QLabel("憑證：—")
        sb.addPermanentWidget(self.sb_cert)

        # 自動登入嘗試（若 .env 完整）
        self._auto_login()

    # ------------------------------------------------------------ tabs
    def _build_tabs(self) -> None:
        # 庫存
        self.page_inv = TablePage(
            title="庫存",
            columns=[
                ("symbol", "代號"),
                ("name", "名稱"),
                ("order_type", "類別"),
                ("total_qty", "總庫存"),
                ("today_qty", "今可賣"),
                ("avg_price", "均價"),
                ("market_value", "市值"),
            ],
            fetcher=lambda force: self._require_svc().inventories(force=force),
            auto_refresh_ms=30_000,
            money_keys={"avg_price", "market_value"},
            export_kind="inventories",
        )
        self.tabs.addTab(self.page_inv, "庫存")

        # 未實現
        self.page_unr = TablePage(
            title="未實現損益",
            columns=[
                ("symbol", "代號"),
                ("name", "名稱"),
                ("order_type", "類別"),
                ("qty", "數量"),
                ("avg_price", "均價"),
                ("last_price", "現價"),
                ("pnl", "未實現損益"),
                ("pnl_rate", "報酬率"),
            ],
            fetcher=lambda force: self._require_svc().unrealized(force=force),
            auto_refresh_ms=30_000,
            money_keys={"avg_price", "last_price", "pnl"},
            color_keys={"pnl", "pnl_rate"},
            export_kind="unrealized",
        )
        self.tabs.addTab(self.page_unr, "未實現")

        # 已實現（含日期選擇）
        self.page_real = self._build_realized_page()
        self.tabs.addTab(self.page_real, "已實現")

        # 現金 / 交割
        self.tabs.addTab(self._build_cash_page(), "現金/交割")

        # 維持率
        self.page_maint = TablePage(
            title="融資融券維持率",
            columns=[
                ("account", "帳號"),
                ("maintenance_rate", "維持率(%)"),
                ("margin_value", "融資市值"),
                ("short_value", "融券市值"),
                ("warning_line", "警示線"),
            ],
            fetcher=lambda force: self._require_svc().maintenance(force=force),
            auto_refresh_ms=60_000,
            money_keys={"margin_value", "short_value", "maintenance_rate"},
            export_kind="maintenance",
        )
        self.tabs.addTab(self.page_maint, "維持率")

        # 即時行情
        self.page_quote = QuotePage(client=self.client)
        self.tabs.addTab(self.page_quote, "即時行情")

        # 下單
        self.page_order = OrderPage(client=self.client)
        self.tabs.addTab(self.page_order, "下單")

    def _build_realized_page(self) -> QWidget:
        today = date.today()
        ed_from = QDateEdit(QDate(today.year, today.month, today.day).addDays(-30))
        ed_to = QDateEdit(QDate(today.year, today.month, today.day))
        for e in (ed_from, ed_to):
            e.setCalendarPopup(True)
            e.setDisplayFormat("yyyy-MM-dd")
        self._ed_from = ed_from
        self._ed_to = ed_to

        def _fetch(force: bool) -> Any:
            qf: date = ed_from.date().toPython()  # type: ignore[assignment]
            qt_: date = ed_to.date().toPython()  # type: ignore[assignment]
            return self._require_svc().realized(qf, qt_)

        page = TablePage(
            title="已實現損益",
            columns=[
                ("trade_date", "日期"),
                ("symbol", "代號"),
                ("name", "名稱"),
                ("order_type", "類別"),
                ("qty", "數量"),
                ("buy_price", "買進價"),
                ("sell_price", "賣出價"),
                ("pnl", "損益"),
                ("fee", "手續費"),
                ("tax", "交易稅"),
            ],
            fetcher=_fetch,
            money_keys={"buy_price", "sell_price", "pnl", "fee", "tax"},
            color_keys={"pnl"},
            export_kind="realized",
        )

        date_bar = QHBoxLayout()
        date_bar.addWidget(QLabel("日期範圍："))
        date_bar.addWidget(ed_from)
        date_bar.addWidget(QLabel("~"))
        date_bar.addWidget(ed_to)
        btn = QPushButton("查詢")
        btn.clicked.connect(lambda: page.refresh(force=True))
        date_bar.addWidget(btn)
        date_bar.addStretch(1)

        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.addLayout(date_bar)
        lay.addWidget(page, 1)
        return wrap

    # ------------------------------------------------------------ login
    def _auto_login(self) -> None:
        try:
            self.client.login()
        except FubonError as exc:
            logger.bind(event="LOGIN_AUTO").warning(f"auto login skipped: {exc}")
            return
        self._on_logged_in()

    def _do_login(self) -> None:
        dlg = LoginDialog(self)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        try:
            self._apply_login_settings(dlg.resolved_settings())
            self.client.login()
        except FubonError as exc:
            QMessageBox.critical(self, "登入失敗", str(exc))
            return
        self._on_logged_in()

    def _apply_login_settings(self, settings: Settings) -> None:
        if self.page_quote.rt is not None:
            self.page_quote._on_close_clicked()
        self.client.logout()
        self.settings = settings
        self.client.settings = settings
        self.svc = None
        self.lbl_login.setText("未登入")
        self.cbx_account.blockSignals(True)
        self.cbx_account.clear()
        self.cbx_account.blockSignals(False)
        self.sb_cert.setText("憑證：—")
        self.page_order._refresh_dry_label()

    def _on_logged_in(self) -> None:
        self.svc = StockAccount(client=self.client, audit=self.audit)
        self.lbl_login.setText(f"✅ {self.client.settings.personal_id}")
        self.cbx_account.blockSignals(True)
        self.cbx_account.clear()
        for a in self.client.accounts:
            self.cbx_account.addItem(a.display, userData=a)
            if self.client.is_logged_in and a is self.client.account:
                self.cbx_account.setCurrentIndex(self.cbx_account.count() - 1)
        self.cbx_account.blockSignals(False)

        if self.client.cert_info:
            ci = self.client.cert_info
            self.sb_cert.setText(
                f"憑證剩餘 {ci.days_left} 天（到期 {ci.not_after.date()}）"
            )
        self._refresh_all()

    def _on_account_changed(self, idx: int) -> None:
        if idx < 0 or not self.client.is_logged_in:
            return
        acc = self.cbx_account.currentData()
        if acc is None:
            return
        try:
            self.client.select_account(acc.branch_no, acc.account)
        except FubonError as exc:
            QMessageBox.warning(self, "切換帳號失敗", str(exc))
            return
        # 清 cache 後全部重整
        if self.svc is not None:
            self.svc.cache.invalidate()
        self._refresh_all()

    def _refresh_all(self) -> None:
        if self.svc is None:
            return
        for page in (self.page_inv, self.page_unr, self.page_cash, self.page_maint):
            page.refresh(force=False)

    def _build_cash_page(self) -> QWidget:
        """建立現金/交割頁：頂部顯示交割餘額摘要，下方為明細表格。"""
        # 摘要標籤
        self._lbl_bank_balance = QLabel("交割餘額：—")
        self._lbl_net_balance = QLabel("扣除未交割後餘額：—")
        for lbl in (self._lbl_bank_balance, self._lbl_net_balance):
            lbl.setStyleSheet("font-size: 14pt; font-weight: bold; padding: 4px 12px;")

        # 初始化供 _load_cash 存放的暫存值
        self._cash_bp: Any = None
        self._cash_setts: list[Any] = []

        self.page_cash = TablePage(
            title="買進力 / 交割明細",
            columns=[
                ("kind", "項目"),
                ("t_date", "日期"),
                ("amount", "金額"),
            ],
            fetcher=self._load_cash,
            auto_refresh_ms=30_000,
            money_keys={"amount"},
            export_kind="cash",
            post_load=self._update_cash_summary,
        )

        summary_bar = QHBoxLayout()
        summary_bar.addWidget(self._lbl_bank_balance)
        summary_bar.addSpacing(40)
        summary_bar.addWidget(self._lbl_net_balance)
        summary_bar.addStretch(1)

        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.addLayout(summary_bar)
        lay.addWidget(self.page_cash, 1)
        return wrap

    def _update_cash_summary(self, _: Any) -> None:
        """在主執行緒更新交割餘額摘要標籤（由 post_load callback 調用）。"""
        bp = self._cash_bp
        setts = self._cash_setts
        if bp is None:
            return
        net = bp.cash + sum(s.amount for s in setts)

        def _fmt(v: Any) -> str:
            try:
                return f"{float(v):,.0f}"
            except (TypeError, ValueError):
                return str(v)

        self._lbl_bank_balance.setText(f"交割餘額：{_fmt(bp.cash)} 元")
        self._lbl_net_balance.setText(f"扣除未交割後餘額：{_fmt(net)} 元")

    # ------------------------------------------------------------ services
    def _require_svc(self) -> StockAccount:
        if self.svc is None:
            raise FubonError("尚未登入")
        return self.svc

    def _load_cash(self, force: bool) -> list[dict[str, Any]]:
        """整合 buying_power + settlements 成單一表。"""
        svc = self._require_svc()
        bp = svc.buying_power(force=force)
        setts = svc.settlements(force=force)
        # 儲存以供主執行緒的 _update_cash_summary 讀取
        self._cash_bp = bp
        self._cash_setts = setts
        rows: list[dict[str, Any]] = [
            {"kind": "現金", "t_date": "-", "amount": bp.cash},
            {"kind": "買進力", "t_date": "-", "amount": bp.buying_power},
        ]
        for s in setts:
            rows.append({"kind": "交割款", "t_date": s.t_date.isoformat(), "amount": s.amount})
        return rows

    # ------------------------------------------------------------ close
    def closeEvent(self, event: Any) -> None:
        """關窗時主動 close 即時行情連線。"""
        try:
            if getattr(self, "page_quote", None) is not None and self.page_quote.rt is not None:
                self.page_quote.rt.close()
        except Exception as exc:  # pragma: no cover
            logger.bind(event="GUI_CLOSE").warning(f"close realtime failed: {exc}")
        super().closeEvent(event)
