# QDate 用于设置记录日期默认值。
from PySide6.QtCore import QDate
# 这些控件用于构建批量添加开票/收款记录的弹窗。
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
    QTabWidget,
)


# 批量添加开票记录和收款记录的弹窗。
class RecordForm(QDialog):
    # 初始化弹窗，显示合同名称，并创建两个记录页签。
    def __init__(self, contract_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"添加记录 - {contract_name}")
        self.setMinimumSize(720, 430)

        self.tabs = QTabWidget()
        self.invoice_table = self._create_record_table()
        self.payment_table = self._create_record_table()

        self.tabs.addTab(self._create_tab(self.invoice_table), "开票记录")
        self.tabs.addTab(self._create_tab(self.payment_table), "收款记录")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

        self._add_record_row(self.invoice_table)
        self._add_record_row(self.payment_table)

    # 创建单个页签，包含表格和新增/删除行按钮。
    def _create_tab(self, table: QTableWidget) -> QWidget:
        widget = QWidget()

        add_button = QPushButton("新增一行")
        add_button.clicked.connect(lambda: self._add_record_row(table))

        remove_button = QPushButton("删除选中行")
        remove_button.clicked.connect(lambda: self._remove_selected_row(table))

        button_row = QHBoxLayout()
        button_row.addWidget(add_button)
        button_row.addWidget(remove_button)
        button_row.addStretch()

        layout = QVBoxLayout(widget)
        layout.addLayout(button_row)
        layout.addWidget(table)
        return widget

    # 创建记录表格，列为日期、金额和备注。
    def _create_record_table(self) -> QTableWidget:
        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["日期", "金额", "备注"])
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    # 给指定表格新增一行可编辑记录。
    def _add_record_row(self, table: QTableWidget) -> None:
        row = table.rowCount()
        table.insertRow(row)

        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        date_edit.setDisplayFormat("yyyy-MM-dd")
        date_edit.setDate(QDate.currentDate())

        amount_edit = QDoubleSpinBox()
        amount_edit.setRange(0, 999999999999.99)
        amount_edit.setDecimals(2)
        amount_edit.setSingleStep(1000)
        amount_edit.setPrefix("¥ ")

        remark_edit = QLineEdit()

        table.setCellWidget(row, 0, date_edit)
        table.setCellWidget(row, 1, amount_edit)
        table.setCellWidget(row, 2, remark_edit)

    # 删除当前选中的记录行。
    def _remove_selected_row(self, table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)

    # 保存前要求至少有一条金额大于 0 的记录。
    def validate_and_accept(self) -> None:
        if not self.invoice_records() and not self.payment_records():
            QMessageBox.warning(self, "提示", "请至少添加一条金额大于 0 的开票或收款记录。")
            return
        self.accept()

    # 返回开票记录列表。
    def invoice_records(self) -> list[dict]:
        return self._records_from_table(self.invoice_table)

    # 返回收款记录列表。
    def payment_records(self) -> list[dict]:
        return self._records_from_table(self.payment_table)

    # 从表格控件中提取有效记录；金额为 0 的空行会被忽略。
    def _records_from_table(self, table: QTableWidget) -> list[dict]:
        records = []
        for row in range(table.rowCount()):
            date_edit = table.cellWidget(row, 0)
            amount_edit = table.cellWidget(row, 1)
            remark_edit = table.cellWidget(row, 2)
            amount = float(amount_edit.value())

            if amount <= 0:
                continue

            records.append(
                {
                    "record_date": date_edit.date().toString("yyyy-MM-dd"),
                    "amount": amount,
                    "remark": remark_edit.text().strip(),
                }
            )
        return records
