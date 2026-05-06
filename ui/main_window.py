# os 用于把文件路径转换为系统可识别的格式。
import os
# Path 用于检查合同文件路径是否存在。
from pathlib import Path

# Qt/QUrl 提供表格对齐、颜色常量和本地文件 URL。
from PySide6.QtCore import Qt, QUrl
# QDesktopServices 用于调用系统默认程序打开合同文件。
from PySide6.QtGui import QDesktopServices
# 这些控件用于构建主窗口、工具栏、搜索框和合同表格。
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

# Database 提供合同查询、统计和增删改查能力。
from database import Database
# Contract 是主窗口中保存合同列表时使用的数据类型。
from models import Contract
# ContractForm 是新增和编辑合同的弹窗。
from ui.contract_form import ContractForm
# StatsWindow 是合同统计弹窗。
from ui.stats_window import StatsWindow


class MainWindow(QMainWindow):
    columns = [
        "ID",
        "合同名称",
        "合同编号",
        "对方名称",
        "金额",
        "签订日期",
        "开始日期",
        "截止日期",
        "状态",
        "文件路径",
        "备注",
    ]

    def __init__(self, database: Database) -> None:
        super().__init__()
        self.database = database
        self.contracts: list[Contract] = []

        self.setWindowTitle("合同管理系统")
        self.resize(1180, 720)

        self._build_ui()
        self._apply_styles()
        self.refresh_table()
        self.show_expiring_reminder()

    def _build_ui(self) -> None:
        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索合同名称、合同编号、对方名称")
        self.search_edit.returnPressed.connect(self.refresh_table)

        search_button = QPushButton("搜索")
        search_button.clicked.connect(self.refresh_table)

        reset_button = QPushButton("重置")
        reset_button.clicked.connect(self.reset_search)

        add_button = QPushButton("新增")
        add_button.clicked.connect(self.add_contract)

        edit_button = QPushButton("编辑")
        edit_button.clicked.connect(self.edit_selected_contract)

        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self.delete_selected_contract)

        stats_button = QPushButton("统计")
        stats_button.clicked.connect(self.open_stats)

        toolbar.addWidget(QLabel("关键词："))
        toolbar.addWidget(self.search_edit)
        toolbar.addWidget(search_button)
        toolbar.addWidget(reset_button)
        toolbar.addSeparator()
        toolbar.addWidget(add_button)
        toolbar.addWidget(edit_button)
        toolbar.addWidget(delete_button)
        toolbar.addSeparator()
        toolbar.addWidget(stats_button)

        self.summary_label = QLabel()

        self.table = QTableWidget(0, len(self.columns))
        self.table.setHorizontalHeaderLabels(self.columns)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_selected_file)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)

        hint = QLabel("提示：双击合同所在行可打开扫描件或 PDF 文件。")
        hint.setObjectName("hintLabel")

        content = QWidget()
        layout = QVBoxLayout(content)
        top_row = QHBoxLayout()
        top_row.addWidget(self.summary_label)
        top_row.addStretch()
        layout.addLayout(top_row)
        layout.addWidget(self.table)
        layout.addWidget(hint)
        self.setCentralWidget(content)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow {
                background: #f6f7f9;
            }
            QToolBar {
                background: #ffffff;
                border-bottom: 1px solid #d9dee7;
                spacing: 8px;
                padding: 8px;
            }
            QLineEdit {
                min-width: 280px;
                padding: 7px 9px;
                border: 1px solid #cbd3df;
                border-radius: 4px;
                background: #ffffff;
            }
            QPushButton {
                padding: 7px 13px;
                border: 1px solid #b9c3d1;
                border-radius: 4px;
                background: #ffffff;
            }
            QPushButton:hover {
                background: #eef4ff;
                border-color: #7aa7e8;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #d9dee7;
                gridline-color: #edf0f5;
                selection-background-color: #d8e8ff;
                selection-color: #1f2937;
            }
            QHeaderView::section {
                background: #eef1f5;
                border: 0;
                border-right: 1px solid #d9dee7;
                border-bottom: 1px solid #d9dee7;
                padding: 8px;
                font-weight: 600;
            }
            QLabel#hintLabel {
                color: #667085;
            }
            QLabel#totalLabel {
                font-size: 18px;
                font-weight: 700;
                padding: 8px 0;
            }
            """
        )

    def refresh_table(self) -> None:
        keyword = self.search_edit.text()
        self.contracts = self.database.list_contracts(keyword)
        self.table.setRowCount(len(self.contracts))

        for row, contract in enumerate(self.contracts):
            values = [
                contract.id,
                contract.contract_name,
                contract.contract_number,
                contract.party_name,
                f"¥ {contract.amount:,.2f}",
                contract.sign_date,
                contract.start_date,
                contract.end_date,
                contract.status,
                contract.file_path,
                contract.remark,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (0, 4, 5, 6, 7, 8):
                    item.setTextAlignment(Qt.AlignCenter)
                item.setData(Qt.UserRole, contract.id)
                self.table.setItem(row, column, item)

            status_item = self.table.item(row, 8)
            if contract.status == "已到期":
                status_item.setForeground(Qt.red)
            elif contract.status == "即将到期":
                status_item.setForeground(Qt.darkYellow)
            else:
                status_item.setForeground(Qt.darkGreen)

        self.summary_label.setText(
            f"共 {len(self.contracts)} 份合同    总金额：¥ {self.database.total_amount():,.2f}"
        )

    def show_expiring_reminder(self) -> None:
        expiring = self.database.expiring_contracts(30)
        if not expiring:
            return
        names = "\n".join(
            f"{item.contract_name}（{item.contract_number}，截止 {item.end_date}）"
            for item in expiring[:8]
        )
        extra = "" if len(expiring) <= 8 else f"\n等共 {len(expiring)} 份合同"
        QMessageBox.information(self, "到期提醒", f"以下合同将在 30 天内到期：\n\n{names}{extra}")

    def reset_search(self) -> None:
        self.search_edit.clear()
        self.refresh_table()

    def selected_contract_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.text()) if item else None

    def add_contract(self) -> None:
        dialog = ContractForm(self)
        if dialog.exec():
            self.database.add_contract(dialog.get_contract())
            self.refresh_table()

    def edit_selected_contract(self) -> None:
        contract_id = self.selected_contract_id()
        if contract_id is None:
            QMessageBox.information(self, "提示", "请先选择一份合同。")
            return

        contract = self.database.get_contract(contract_id)
        if contract is None:
            QMessageBox.warning(self, "提示", "未找到该合同，列表将刷新。")
            self.refresh_table()
            return

        dialog = ContractForm(self, contract)
        if dialog.exec():
            self.database.update_contract(dialog.get_contract())
            self.refresh_table()

    def delete_selected_contract(self) -> None:
        contract_id = self.selected_contract_id()
        if contract_id is None:
            QMessageBox.information(self, "提示", "请先选择一份合同。")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除选中的合同吗？此操作不可撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.database.delete_contract(contract_id)
            self.refresh_table()

    def open_selected_file(self) -> None:
        contract_id = self.selected_contract_id()
        if contract_id is None:
            return

        contract = self.database.get_contract(contract_id)
        if not contract or not contract.file_path:
            QMessageBox.information(self, "提示", "该合同尚未保存文件路径。")
            return

        path = Path(contract.file_path)
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"找不到文件：\n{contract.file_path}")
            return

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(path))):
            QMessageBox.warning(self, "打开失败", "系统无法打开该文件。")

    def open_stats(self) -> None:
        dialog = StatsWindow(self.database, self)
        dialog.exec()
        self.refresh_table()
