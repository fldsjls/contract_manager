from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from database import Database


class LoginDialog(QDialog):
    # 登录弹窗：支持账号密码登录和游客模式。
    def __init__(self, database: Database, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.is_guest = False

        self.setWindowTitle("登录 - 合同管理系统")
        self.setMinimumWidth(360)

        title = QLabel("合同管理系统")
        title.setObjectName("pageTitle")

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("账号")
        self.username_edit.setText("admin")

        self.password_edit = QLineEdit()
        self.password_edit.setPlaceholderText("密码")
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.returnPressed.connect(self.login)

        login_button = QPushButton("登录")
        login_button.clicked.connect(self.login)

        guest_button = QPushButton("游客模式")
        guest_button.clicked.connect(self.enter_as_guest)

        button_row = QHBoxLayout()
        button_row.addWidget(login_button)
        button_row.addWidget(guest_button)

        tip = QLabel("请输入账号和密码登录")
        tip.setObjectName("hintLabel")

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.username_edit)
        layout.addWidget(self.password_edit)
        layout.addLayout(button_row)
        layout.addWidget(tip)

    # 校验账号密码，成功后进入完整功能模式。
    def login(self) -> None:
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if self.database.verify_user(username, password):
            self.is_guest = False
            self.accept()
            return
        QMessageBox.warning(self, "登录失败", "账号或密码不正确。")

    # 游客模式进入系统；只允许查看，不允许修改数据。
    def enter_as_guest(self) -> None:
        self.is_guest = True
        self.accept()
