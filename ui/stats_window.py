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


# 合同统计弹窗，用于展示总金额、状态数量和即将到期合同。
class StatsWindow(QDialog):
    # 初始化统计窗口，并立即刷新统计数据。
    def __init__(self, database: Database, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.setWindowTitle("合同统计")
        self.setMinimumSize(520, 360)

        # 总金额标签会显示所有合同金额合计。
        self.total_label = QLabel()
        self.total_label.setObjectName("totalLabel")

        # 状态统计使用网格展示，便于横向排列“进行中/即将到期/已到期”。
        self.status_grid = QGridLayout()

        # 即将到期合同使用表格展示，禁止在统计窗口中直接编辑。
        self.expiring_table = QTableWidget(0, 4)
        self.expiring_table.setHorizontalHeaderLabels(["合同名称", "合同编号", "对方名称", "截止日期"])
        self.expiring_table.horizontalHeader().setStretchLastSection(True)
        self.expiring_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.expiring_table.setSelectionBehavior(QTableWidget.SelectRows)

        # 从上到下排列总金额、状态统计、即将到期标题和表格。
        layout = QVBoxLayout(self)
        layout.addWidget(self.total_label)
        layout.addLayout(self.status_grid)
        layout.addWidget(QLabel("30 天内即将到期"))
        layout.addWidget(self.expiring_table)

        self.refresh()

    # 重新读取统计数据，并刷新窗口中的标签、状态网格和到期表格。
    def refresh(self) -> None:
        self.total_label.setText(f"合同总金额：¥ {self.database.total_amount():,.2f}")

        # 刷新前先清空旧的状态统计控件，避免重复叠加。
        while self.status_grid.count():
            item = self.status_grid.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        # 按状态生成统计数字，并放入网格。
        counts = self.database.count_by_status()
        for column, (status, count) in enumerate(counts.items()):
            title = QLabel(status)
            number = QLabel(str(count))
            title.setAlignment(Qt.AlignCenter)
            number.setAlignment(Qt.AlignCenter)
            number.setStyleSheet("font-size: 24px; font-weight: 700;")
            self.status_grid.addWidget(title, 0, column)
            self.status_grid.addWidget(number, 1, column)

        # 查询 30 天内即将到期的合同，并填入表格。
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
