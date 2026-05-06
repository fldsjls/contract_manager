# os 用于把文件路径转换为系统可识别的格式。
import os
# Path 用于检查合同文件路径是否存在。
from pathlib import Path
# Qt/QUrl 提供表格对齐、颜色常量和本地文件 URL。
from PySide6.QtCore import QEvent, QSize, Qt, QUrl
# QDesktopServices 用于调用系统默认程序打开合同文件。
from PySide6.QtGui import QBrush, QDesktopServices
# 这些控件用于构建主窗口、工具栏、搜索框和可展开合同列表。
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
# Database 提供合同查询、统计和增删改查能力。
from database import Database, DuplicateContractNumberError, InvalidContractNumberError
# Contract 是主窗口中保存合同列表时使用的数据类型。
from models import Contract
# ContractForm 是新增和编辑合同的弹窗。
from ui.contract_form import ContractForm
# RecordForm 是批量添加开票/收款记录的弹窗。
from ui.record_form import RecordForm
# RecordsWindow 是查看某份合同全部记录的独立窗口。
from ui.records_window import RecordsWindow
# StatsWindow 是合同统计弹窗。
from ui.stats_window import StatsWindow
# MAIN_WINDOW_STYLE 集中保存主窗口的视觉样式。
from ui.styles import MAIN_WINDOW_STYLE
from ui.file_utils import archive_file


class ContractTreeWidget(QTreeWidget):
    # 点击树形列表空白区域时清空当前选中行。
    def mousePressEvent(self, event) -> None:
        if not self.indexAt(event.position().toPoint()).isValid():
            parent = self.window()
            if hasattr(parent, "clear_table_selection"):
                parent.clear_table_selection()
            else:
                self.clearSelection()
                self.setCurrentItem(None)
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    # 树形列表的列标题；合同是父行，开票/收款记录是子行。
    columns = [
        "ID",
        "合同名称",
        "合同编号",
        "合同类型",
        "甲方名称",
        "金额",
        "是否开局发票",
        "签订日期",
        "开始日期",
        "截止日期",
        "状态",
        "备注",
    ]

    # 初始化主窗口，接收数据库对象并完成界面创建、样式设置和数据加载。
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

    # 创建主窗口中的工具栏、搜索框、按钮、树形列表和底部提示。
    def _build_ui(self) -> None:
        toolbar = QToolBar("工具栏")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索合同名称、合同编号、甲方名称")
        self.search_edit.returnPressed.connect(self.refresh_table)

        search_button = QPushButton("搜索")
        search_button.clicked.connect(self.refresh_table)

        reset_button = QPushButton("重置")
        reset_button.clicked.connect(self.reset_search)

        add_button = QPushButton("新增")
        add_button.clicked.connect(self.add_contract)

        self.edit_button = QPushButton("编辑")
        self.edit_button.setEnabled(False)
        self.edit_button.clicked.connect(self.edit_selected_contract)

        self.open_file_button = QPushButton("打开文件")
        self.open_file_button.setEnabled(False)
        self.open_file_button.clicked.connect(self.open_selected_file)

        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self.delete_selected_contract)

        self.add_record_button = QPushButton("添加记录")
        self.add_record_button.setEnabled(False)
        self.add_record_button.clicked.connect(self.add_records)

        self.view_records_button = QPushButton("查看记录")
        self.view_records_button.setEnabled(False)
        self.view_records_button.clicked.connect(self.view_records)

        stats_button = QPushButton("统计")
        stats_button.clicked.connect(self.open_stats)

        toolbar.addWidget(QLabel("关键词："))
        toolbar.addWidget(self.search_edit)
        toolbar.addWidget(search_button)
        toolbar.addWidget(reset_button)
        toolbar.addSeparator()
        toolbar.addWidget(add_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(delete_button)
        toolbar.addSeparator()
        toolbar.addWidget(self.add_record_button)
        toolbar.addWidget(self.open_file_button)
        toolbar.addWidget(self.view_records_button)
        toolbar.addWidget(stats_button)

        self.summary_label = QLabel()

        self.table = ContractTreeWidget()
        self.table.setColumnCount(len(self.columns))
        self.table.setHeaderLabels(self.columns)
        for column in range(len(self.columns)):
            self.table.headerItem().setTextAlignment(column, Qt.AlignCenter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.itemDoubleClicked.connect(lambda _item, _column: self.edit_selected_contract())
        self.table.itemSelectionChanged.connect(self.update_action_buttons)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setRootIsDecorated(False)
        self.table.setItemsExpandable(True)
        self.table.setIndentation(0)
        self.table.setUniformRowHeights(True)
        self.table.setSortingEnabled(True)
        self.table.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.header().setSectionResizeMode(11, QHeaderView.Stretch)

        hint = QLabel("提示：双击合同所在行可编辑合同；选中合同后可添加或查看记录。")
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

        content.installEventFilter(self)
        self.summary_label.installEventFilter(self)
        hint.installEventFilter(self)

    # 设置窗口、工具栏、按钮、树形列表等控件的整体视觉样式。
    def _apply_styles(self) -> None:
        self.setStyleSheet(MAIN_WINDOW_STYLE)

    # 按当前搜索关键词重新查询合同，并把合同和子记录填入树形列表。
    def refresh_table(self) -> None:
        keyword = self.search_edit.text()
        self.contracts = self.database.list_contracts(keyword)
        self.table.setSortingEnabled(False)
        self.table.clear()

        for contract in self.contracts:
            contract_item = self._create_contract_item(contract)
            self.table.addTopLevelItem(contract_item)

            # 如需在合同下方展开显示开票/收款子记录，可以取消下面两行注释。
            # for record in self.database.list_contract_records(contract.id or 0):
            #     contract_item.addChild(self._create_record_item(record, contract.id or 0))

        self.summary_label.setText(
            f"共 {len(self.contracts)} 份合同    总金额：¥ {self.database.total_amount():,.2f}"
        )
        self.table.setSortingEnabled(True)
        self.clear_table_selection()

    # 把一份合同转换为树形列表中的父行。
    def _create_contract_item(self, contract: Contract) -> QTreeWidgetItem:
        values = [
            contract.id,
            contract.contract_name,
            contract.contract_number,
            contract.contract_type,
            contract.party_name,
            f"¥ {contract.amount:,.2f}",
            contract.invoice_status,
            contract.sign_date,
            contract.start_date,
            contract.end_date,
            contract.status,
            contract.remark,
        ]
        item = QTreeWidgetItem([str(value) for value in values])
        item.setData(0, Qt.UserRole, contract.id)
        item.setData(5, Qt.EditRole, contract.amount)
        item.setSizeHint(0, QSize(0, 28))

        for column in (0, 3, 5, 6, 7, 8, 9, 10):
            item.setTextAlignment(column, Qt.AlignCenter)

        if contract.status == "已到期":
            item.setForeground(10, QBrush(Qt.red))
        elif contract.status == "即将到期":
            item.setForeground(10, QBrush(Qt.darkYellow))
        else:
            item.setForeground(10, QBrush(Qt.darkGreen))

        return item

    # 把一条开票或收款记录转换为合同父行下面的子行。
    def _create_record_item(self, record, contract_id: int) -> QTreeWidgetItem:
        amount = float(record["amount"] or 0)
        values = [
            record["record_type"],
            record["record_date"],
            "",
            "",
            "",
            f"¥ {amount:,.2f}",
            "",
            "",
            "",
            "",
            "",
            record["remark"] or "",
        ]
        item = QTreeWidgetItem([str(value) for value in values])
        item.setData(0, Qt.UserRole, contract_id)
        item.setData(5, Qt.EditRole, amount)
        item.setSizeHint(0, QSize(0, 22))

        color = Qt.darkBlue if record["record_type"] == "开票记录" else Qt.darkGreen
        item.setForeground(0, QBrush(color))
        item.setTextAlignment(0, Qt.AlignCenter)
        item.setTextAlignment(1, Qt.AlignLeft | Qt.AlignVCenter)
        item.setTextAlignment(5, Qt.AlignCenter)
        return item

    # 程序启动时提醒 30 天内即将到期的合同。
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

    # 清空搜索框并恢复显示全部合同。
    def reset_search(self) -> None:
        self.search_edit.clear()
        self.refresh_table()

    # 捕获主内容区空白点击，用于取消当前选中行。
    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.MouseButtonPress and watched is not self.table:
            self.clear_table_selection()
        return super().eventFilter(watched, event)

    # 清空树形列表当前选择，并同步禁用依赖选中行的按钮。
    def clear_table_selection(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentItem(None)
        self.update_action_buttons()

    # 读取当前选中行对应的合同 ID；选中子记录时会回到它所属的合同。
    def selected_contract_id(self) -> int | None:
        selection = self.table.selectionModel()
        if selection is None or not selection.hasSelection():
            return None

        item = self.table.currentItem()
        if item is None:
            return None
        if item.parent() is not None:
            item = item.parent()

        contract_id = item.data(0, Qt.UserRole)
        return int(contract_id) if contract_id is not None else None

    # 根据当前是否选中合同，启用或禁用编辑、打开文件、添加记录和查看记录按钮。
    def update_action_buttons(self) -> None:
        has_selection = self.selected_contract_id() is not None
        self.edit_button.setEnabled(has_selection)
        self.open_file_button.setEnabled(has_selection)
        self.add_record_button.setEnabled(has_selection)
        self.view_records_button.setEnabled(has_selection)

    # 打开新增合同弹窗，保存成功后刷新树形列表。
    def add_contract(self) -> None:
        dialog = ContractForm(self)
        if dialog.exec():
            contract = dialog.get_contract()
            contract.file_path = archive_file(contract.file_path, contract.contract_name)
            try:
                self.database.add_contract(contract)
            except InvalidContractNumberError:
                QMessageBox.warning(self, "合同编号格式错误", "合同编号必须是 12 位数字。")
                return
            except DuplicateContractNumberError:
                QMessageBox.warning(self, "合同编号重复", "该合同编号已存在，请修改后再保存。")
                return
            self.refresh_table()

    # 打开编辑合同弹窗，保存成功后更新数据库并刷新树形列表。
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
            updated_contract = dialog.get_contract()
            updated_contract.file_path = archive_file(
                updated_contract.file_path,
                updated_contract.contract_name,
            )
            try:
                self.database.update_contract(updated_contract)
            except InvalidContractNumberError:
                QMessageBox.warning(self, "合同编号格式错误", "合同编号必须是 12 位数字。")
                return
            except DuplicateContractNumberError:
                QMessageBox.warning(self, "合同编号重复", "该合同编号已存在，请修改后再保存。")
                return
            self.refresh_table()

    # 删除当前选中的合同，删除前先要求用户确认。
    def delete_selected_contract(self) -> None:
        contract_id = self.selected_contract_id()
        if contract_id is None:
            QMessageBox.information(self, "提示", "请先选择一份合同。")
            return

        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除选中的合同吗？此操作不可撤销。\n该合同下的开票记录和收款记录也会一起删除。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.database.delete_contract(contract_id)
            self.refresh_table()

    # 选中合同后，用系统默认程序打开已保存的合同文件。
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

    # 为当前合同批量添加开票记录和收款记录，保存后刷新子条目。
    def add_records(self) -> None:
        contract_id = self.selected_contract_id()
        if contract_id is None:
            QMessageBox.information(self, "提示", "请先选择一份合同。")
            return

        contract = self.database.get_contract(contract_id)
        if contract is None:
            QMessageBox.warning(self, "提示", "未找到该合同，列表将刷新。")
            self.refresh_table()
            return

        dialog = RecordForm(contract.contract_name, contract.invoice_status, self)
        if dialog.exec():
            self.database.add_invoice_records(
                contract_id,
                self._archive_record_files(dialog.invoice_records(), contract.contract_name),
            )
            self.database.add_payment_records(
                contract_id,
                self._archive_record_files(dialog.payment_records(), contract.contract_name),
            )
            self.refresh_table()
            QMessageBox.information(self, "保存成功", "开票记录和收款记录已保存，可展开合同查看。")

    # 将开票/收款记录中的附件复制到合同目录，并把记录路径替换为复制后的路径。
    def _archive_record_files(self, records: list[dict], contract_name: str) -> list[dict]:
        archived_records = []
        for record in records:
            archived = dict(record)
            archived["file_path"] = archive_file(archived.get("file_path", ""), contract_name)
            archived_records.append(archived)
        return archived_records

    # 打开独立记录查看窗口，按页签查看开票和收款明细。
    def view_records(self) -> None:
        contract_id = self.selected_contract_id()
        if contract_id is None:
            QMessageBox.information(self, "提示", "请先选择一份合同。")
            return

        contract = self.database.get_contract(contract_id)
        if contract is None:
            QMessageBox.warning(self, "提示", "未找到该合同，列表将刷新。")
            self.refresh_table()
            return

        dialog = RecordsWindow(self.database, contract_id, contract.contract_name, self)
        dialog.exec()
        self.refresh_table()

    # 打开统计窗口，关闭后刷新主列表以同步最新状态。
    def open_stats(self) -> None:
        dialog = StatsWindow(self.database, self)
        dialog.exec()
        self.refresh_table()
