# date/datetime 用于处理默认日期和默认合同编号。
from datetime import date, datetime
# QDate 是 PySide6 的日期类型，配合日期选择控件使用。
from PySide6.QtCore import QDate
# 这些控件用于构建新增/编辑合同的弹窗表单。
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


# Contract 是表单保存后返回给数据库层的数据对象。
from database import InvalidContractNumberError, validate_contract_number
from models import Contract
from ui.file_utils import COMMON_FILE_FILTER


CONTRACT_TYPES = ["维保", "评估", "检测", "改造", "新建", "其他项目"]
EMPTY_DATE = QDate(1900, 1, 1)


# 新增/编辑合同使用的弹窗表单。
class ContractForm(QDialog):
    # 初始化表单；传入 contract 时表示编辑，否则表示新增。
    def __init__(self, parent: QWidget | None = None, contract: Contract | None = None) -> None:
        super().__init__(parent)
        self.contract = contract
        self.setWindowTitle("编辑合同" if contract else "新增合同")
        self.setMinimumWidth(520)

        # 基础文本输入框。
        self.name_edit = QLineEdit()
        self.number_edit = QLineEdit()
        self.number_edit.setText(generate_contract_number())
        self.contract_type_edit = QComboBox()
        self.contract_type_edit.addItems(CONTRACT_TYPES)
        self.party_edit = QLineEdit()

        # 金额输入框支持小数，并限制为非负金额。
        self.amount_edit = QDoubleSpinBox()
        self.amount_edit.setRange(0, 999999999999.99)
        self.amount_edit.setDecimals(2)
        self.amount_edit.setSingleStep(1000)
        self.amount_edit.setPrefix("¥ ")

        # 是否开局发票使用固定选项，避免手工输入不一致。
        self.invoice_status_edit = QComboBox()
        self.invoice_status_edit.addItems(["不开票", "开票"])

        # 日期字段统一使用可弹出日历的日期选择控件。
        self.sign_date_edit = self._date_edit()
        self.start_date_edit = self._date_edit()
        self.end_date_edit = self._date_edit(allow_empty=True)
        self.contract_type_edit.currentTextChanged.connect(self.on_contract_type_changed)

        # 文件路径输入框和“选择文件”按钮横向排列。
        self.file_path_edit = QLineEdit()
        browse_button = QPushButton("选择文件")
        browse_button.clicked.connect(self.choose_file)
        file_row = QHBoxLayout()
        file_row.addWidget(self.file_path_edit)
        file_row.addWidget(browse_button)

        self.remark_edit = QTextEdit()
        self.remark_edit.setFixedHeight(90)

        # 表单布局按“标签 + 输入控件”的方式排列字段。
        form = QFormLayout()
        form.addRow("合同名称 *", self.name_edit)
        form.addRow("合同编号 *", self.number_edit)
        form.addRow("合同类型", self.contract_type_edit)
        form.addRow("甲方名称 *", self.party_edit)
        form.addRow("合同金额", self.amount_edit)
        form.addRow("是否开局发票", self.invoice_status_edit)
        form.addRow("签订日期", self.sign_date_edit)
        form.addRow("开始日期", self.start_date_edit)
        form.addRow("截止日期", self.end_date_edit)
        form.addRow("扫描件/PDF", file_row)
        form.addRow("备注", self.remark_edit)

        # 保存按钮先执行校验；取消按钮直接关闭弹窗。
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

        # 新增合同时默认按当前合同类型设置截止日期；非维保类型默认为空。
        self.on_contract_type_changed(self.contract_type_edit.currentText())

        # 编辑模式下，把已有合同内容填入表单。
        if contract:
            self._load_contract(contract)

    # 创建统一格式的日期选择控件。
    def _date_edit(self, allow_empty: bool = False) -> QDateEdit:
        edit = QDateEdit()
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("yyyy-MM-dd")
        if allow_empty:
            edit.setMinimumDate(EMPTY_DATE)
            edit.setSpecialValueText("")
            edit.setDate(EMPTY_DATE)
        else:
            edit.setDate(QDate.currentDate())
        return edit

    # 合同类型改变时，非维保合同默认不填写截止日期。
    def on_contract_type_changed(self, contract_type: str) -> None:
        if contract_type != "维保":
            self.end_date_edit.setDate(EMPTY_DATE)
        elif contract_type == "维保" and self.end_date_edit.date() == EMPTY_DATE:
            self.end_date_edit.setDate(QDate.currentDate())

    # 将已有合同数据加载到表单控件中，用于编辑场景。
    def _load_contract(self, contract: Contract) -> None:
        self.name_edit.setText(contract.contract_name)
        self.number_edit.setText(contract.contract_number)
        self.contract_type_edit.setCurrentText(contract.contract_type or "其他项目")
        self.party_edit.setText(contract.party_name)
        self.amount_edit.setValue(contract.amount)
        self.invoice_status_edit.setCurrentText(contract.invoice_status or "不开票")
        self.sign_date_edit.setDate(to_qdate(contract.sign_date))
        self.start_date_edit.setDate(to_qdate(contract.start_date))
        self.end_date_edit.setDate(to_qdate(contract.end_date, allow_empty=True))
        self.file_path_edit.setText(contract.file_path)
        self.remark_edit.setPlainText(contract.remark)

    # 打开文件选择窗口，并把选中的扫描件/PDF 路径写入输入框。
    def choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择合同文件",
            "",
            COMMON_FILE_FILTER,
        )
        if path:
            self.file_path_edit.setText(path)

    # 保存前校验必填字段，全部通过后才关闭弹窗并返回成功。
    def validate_and_accept(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写合同名称。")
            return
        if not self.number_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写合同编号。")
            return
        try:
            validate_contract_number(self.number_edit.text().strip())
        except InvalidContractNumberError:
            QMessageBox.warning(self, "提示", "合同编号必须是 12 位数字。")
            return
        if not self.party_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写甲方名称。")
            return
        if self.contract_type_edit.currentText() == "维保" and self.end_date_edit.date() == EMPTY_DATE:
            QMessageBox.warning(self, "提示", "维保合同必须填写截止日期。")
            return
        self.accept()

    # 从表单控件读取数据，组装成 Contract 对象交给数据库层保存。
    def get_contract(self) -> Contract:
        contract_id = self.contract.id if self.contract else None
        return Contract(
            id=contract_id,
            contract_name=self.name_edit.text().strip(),
            contract_number=self.number_edit.text().strip(),
            contract_type=self.contract_type_edit.currentText(),
            party_name=self.party_edit.text().strip(),
            amount=float(self.amount_edit.value()),
            invoice_status=self.invoice_status_edit.currentText(),
            sign_date=self.sign_date_edit.date().toString("yyyy-MM-dd"),
            start_date=self.start_date_edit.date().toString("yyyy-MM-dd"),
            end_date=date_to_text(self.end_date_edit.date()),
            file_path=self.file_path_edit.text().strip(),
            remark=self.remark_edit.toPlainText().strip(),
        )


# 将 yyyy-MM-dd 日期文本转换为 QDate；允许为空时返回空日期。
def to_qdate(value: str, allow_empty: bool = False) -> QDate:
    if allow_empty and not value:
        return EMPTY_DATE
    try:
        year, month, day = [int(part) for part in value.split("-")]
        return QDate(year, month, day)
    except (ValueError, AttributeError):
        if allow_empty:
            return EMPTY_DATE
        today = date.today()
        return QDate(today.year, today.month, today.day)


# 将截止日期控件的 QDate 转换成数据库文本；空日期保存为空字符串。
def date_to_text(value: QDate) -> str:
    if value == EMPTY_DATE:
        return ""
    return value.toString("yyyy-MM-dd")


# 生成默认合同编号，格式为 年月日时分，例如 202605061508。
def generate_contract_number() -> str:
    return datetime.now().strftime("%Y%m%d%H%M")
