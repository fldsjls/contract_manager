# Qt 用于设置统计表格中的文本对齐方式。
from PySide6.QtCore import Qt
# 这些控件用于构建合同统计窗口。
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

# Database 提供总金额、状态数量和即将到期合同数据。
from database import Database


class StatsWindow(QDialog):
    def __init__(self, database: Database, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("合同统计")
        self.setMinimumSize(520, 360)

        self.total_label = QLabel()
        self.total_label.setObjectName("totalLabel")

        self.status_grid = QGridLayout()
        self.expiring_table = QTableWidget(0, 4)
        self.expiring_table.setHorizontalHeaderLabels(["合同名称", "合同编号", "对方名称", "截止日期"])
        self.expiring_table.horizontalHeader().setStretchLastSection(True)
        self.expiring_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.expiring_table.setSelectionBehavior(QTableWidget.SelectRows)

        layout = QVBoxLayout(self)
        layout.addWidget(self.total_label)
        layout.addLayout(self.status_grid)
        layout.addWidget(QLabel("30 天内即将到期"))
        layout.addWidget(self.expiring_table)

        self.refresh()

    def refresh(self) -> None:
        self.total_label.setText(f"合同总金额：¥ {self.database.total_amount():,.2f}")

        while self.status_grid.count():
            item = self.status_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        counts = self.database.count_by_status()
        for column, (status, count) in enumerate(counts.items()):
            title = QLabel(status)
            number = QLabel(str(count))
            title.setAlignment(Qt.AlignCenter)
            number.setAlignment(Qt.AlignCenter)
            number.setStyleSheet("font-size: 24px; font-weight: 700;")
            self.status_grid.addWidget(title, 0, column)
            self.status_grid.addWidget(number, 1, column)

        contracts = self.database.expiring_contracts(30)
        self.expiring_table.setRowCount(len(contracts))
        for row, contract in enumerate(contracts):
            values = [
                contract.contract_name,
                contract.contract_number,
                contract.party_name,
                contract.end_date,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.expiring_table.setItem(row, column, item)
