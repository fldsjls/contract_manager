import os

from django.core.asgi import get_asgi_application


# 指定 ASGI 运行时使用的 Django 配置模块。
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "contract_web.settings")

# ASGI 应用对象，供异步服务器部署时调用。
application = get_asgi_application()
