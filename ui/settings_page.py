from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from ui.app_settings import load_settings, save_settings


class SettingsPage(QWidget):
    # 设置页，保存附件归档相关选项。
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.settings = load_settings()

        title = QLabel("设置")
        title.setObjectName("pageTitle")

        self.delete_source_check = QCheckBox("复制附件到合同文件夹后，删除原文件")
        self.delete_source_check.setChecked(
            bool(self.settings.get("delete_source_after_archive", False))
        )
        self.delete_source_check.toggled.connect(self.save)

        tip = QLabel("关闭时：复制后保留原文件；开启时：复制成功后自动删除原文件。")
        tip.setObjectName("hintLabel")

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self.delete_source_check)
        layout.addWidget(tip)
        layout.addStretch()

    # 保存设置。
    def save(self) -> None:
        self.settings["delete_source_after_archive"] = self.delete_source_check.isChecked()
        save_settings(self.settings)

    # 供主窗口读取当前开关状态。
    def delete_source_after_archive(self) -> bool:
        return self.delete_source_check.isChecked()
