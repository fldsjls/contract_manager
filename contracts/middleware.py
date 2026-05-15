from django.shortcuts import redirect


# 限制 Django 后台只允许专用超级管理员模式访问。
class SuperAdminOnlyAdminMiddleware:
    # 保存后续中间件或视图处理函数。
    def __init__(self, get_response):
        self.get_response = get_response

    # 在进入 /admin/ 前统一检查当前会话是否具备后台权限。
    def __call__(self, request):
        if request.path.startswith("/admin/") and not self.has_super_admin_access(request):
            return redirect("contracts:login")
        return self.get_response(request)

    # 判断当前请求是否来自启用超级管理员模式的 superuser。
    @staticmethod
    def has_super_admin_access(request) -> bool:
        user = getattr(request, "user", None)
        return bool(
            user
            and user.is_authenticated
            and user.is_staff
            and user.is_superuser
            and user.username == "superuser"
            and request.session.get("super_admin_mode", False)
        )
