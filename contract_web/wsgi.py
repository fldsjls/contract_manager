import os

from django.core.wsgi import get_wsgi_application


# 指定 WSGI 运行时使用的 Django 配置模块。
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contract_web.settings")

# WSGI 应用对象，供 runserver 或正式 Web 服务器调用。
application = get_wsgi_application()
