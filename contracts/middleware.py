from django.shortcuts import redirect


class SuperAdminOnlyAdminMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/") and not self.has_super_admin_access(request):
            return redirect("contracts:login")
        return self.get_response(request)

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
