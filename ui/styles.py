# 主窗口整体样式，集中管理背景、工具栏、按钮和合同列表外观。
MAIN_WINDOW_STYLE = """
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
QPushButton:disabled {
    color: #98a2b3;
    background: #f2f4f7;
    border-color: #d0d5dd;
}
QTreeWidget {
    background: #ffffff;
    border: 1px solid #d9dee7;
    selection-background-color: #b9d8ff;
    selection-color: #0f172a;
    alternate-background-color: #fafbfc;
}
QTreeWidget::item {
    padding: 2px 6px;
    border-bottom: 1px solid #edf0f5;
}
QTreeWidget::item:selected {
    background: #b9d8ff;
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
QLabel#hintLabel {
    color: #667085;
}
QLabel#totalLabel {
    font-size: 18px;
    font-weight: 700;
    padding: 8px 0;
}
"""


# 记录查看窗口样式，集中管理记录表格和表头外观。
RECORDS_WINDOW_STYLE = """
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
