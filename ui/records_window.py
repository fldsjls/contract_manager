# Qt 提供对齐常量；表格和弹窗控件用于展示某份合同的记录明细。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

# Database 提供开票记录和收款记录的查询方法。
from database import Database


class RecordsTableWidget(QTableWidget):
    # 点击记录表格空白区域时清空当前选中行，和主窗口列表保持一致。
    def mousePressEvent(self, event) -> None:
        if not self.indexAt(event.position().toPoint()).isValid():
            self.clearSelection()
            self.setCurrentItem(None)
        super().mousePressEvent(event)


class RecordsWindow(QDialog):
    # 初始化记录查看窗口，按合同分别显示开票记录和收款记录。
    def __init__(
        self,
        database: Database,
        contract_id: int,
        contract_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.database = database
        self.contract_id = contract_id

        self.setWindowTitle(f"查看记录 - {contract_name}")
        self.setMinimumSize(760, 460)

        self.tabs = QTabWidget()
        self.invoice_table = self._create_table()
        self.payment_table = self._create_table()

        self.tabs.addTab(self._create_tab(self.invoice_table, "开票记录"), "开票记录")
        self.tabs.addTab(self._create_tab(self.payment_table, "收款记录"), "收款记录")

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(contract_name))
        layout.addWidget(self.tabs)

        self._apply_styles()
        self.refresh_records()

    # 创建只读表格，列为日期、金额和备注。
    def _create_table(self) -> QTableWidget:
        table = RecordsTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["日期", "金额", "备注"])
        for column in range(table.columnCount()):
            table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignCenter)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        return table

    # 设置记录查看窗口的选中颜色，让点击记录后整行更明显。
    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QTableWidget {
                background: #ffffff;
                border: 1px solid #d9dee7;
                gridline-color: #edf0f5;
                selection-background-color: #9ec9ff;
                selection-color: #0f172a;
            }
            QTableWidget::item {
                padding: 5px 8px;
            }
            QTableWidget::item:selected {
                background: #9ec9ff;
                color: #0f172a;
                outline: none;
            }
            QHeaderView::section {
                background: #eef1f5;
                border: 0;
                border-right: 1px solid #d9dee7;
                border-bottom: 1px solid #d9dee7;
                padding: 8px;
                font-weight: 600;
            }
            """
        )

    # 给每个页签放入统计文字和对应记录表格。
    def _create_tab(self, table: QTableWidget, title: str) -> QWidget:
        widget = QWidget()
        summary = QLabel()
        summary.setObjectName(f"{title}Summary")
        table.setProperty("summaryLabel", summary)

        layout = QVBoxLayout(widget)
        layout.addWidget(summary)
        layout.addWidget(table)
        return widget

    # 从数据库读取记录，并刷新两个页签的表格内容。
    def refresh_records(self) -> None:
        self._fill_table(self.invoice_table, self.database.list_invoice_records(self.contract_id))
        self._fill_table(self.payment_table, self.database.list_payment_records(self.contract_id))

    # 把数据库记录行填入指定表格，同时更新记录条数和金额合计。
    def _fill_table(self, table: QTableWidget, records) -> None:
        table.setRowCount(len(records))
        total = 0.0

        for row, record in enumerate(records):
            total += float(record["amount"] or 0)
            values = [
                record["record_date"],
                f"¥ {float(record['amount'] or 0):,.2f}",
                record["remark"] or "",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (0, 1):
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, column, item)

        summary = table.property("summaryLabel")
        if summary:
            summary.setText(f"共 {len(records)} 条记录    合计：¥ {total:,.2f}")
