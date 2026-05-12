#!/usr/bin/env python
# Django 命令行入口文件。
import os
import sys


# 执行 manage.py 后面的管理命令。
# 函数说明：封装可复用的业务处理。
def main() -> None:
    # 指定当前项目使用的 Django 配置模块。
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contract_web.settings")
    # 延迟导入 Django 命令执行函数，确保环境变量已设置。
    from django.core.management import execute_from_command_line

    # 把命令行参数交给 Django 处理。
    execute_from_command_line(sys.argv)


# 直接运行 manage.py 时启动命令入口。
if __name__ == "__main__":
    main()
