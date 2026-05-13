from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path


admin.site.site_url = "/operation-logs/?management_open=1"

# 项目总路由，把后台和合同应用路由挂到根路径。
urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("contracts.urls")),
]

# 开发环境下让 Django 直接提供上传文件访问。
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
