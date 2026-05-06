# sys 用来读取程序启动时的命令行参数。
import sys

# QApplication 是 PySide6 应用程序对象，负责启动桌面界面事件循环。
from PySide6.QtWidgets import QApplication

# Database 负责 SQLite 数据库初始化和合同数据操作。
from database import Database
# MainWindow 是合同管理系统的主窗口。
from ui.main_window import MainWindow


def main() -> int:
    # 创建 Qt 应用对象，所有桌面窗口都运行在这个应用里。
    app = QApplication(sys.argv)
    app.setApplicationName("合同管理系统")

    # 初始化数据库后，把数据库对象交给主窗口使用。
    database = Database()
    window = MainWindow(database)
    window.show()

    # 进入 Qt 事件循环，直到用户关闭窗口。
    return app.exec()


if __name__ == "__main__":
    # 直接运行 main.py 时启动程序。
    raise SystemExit(main())
