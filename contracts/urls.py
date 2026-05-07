from django.urls import path

from . import views


# 当前应用的命名空间。
app_name = "contracts"

# 合同应用内部路由配置。
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/password/", views.password_change, name="password_change"),
    path("contracts/", views.contract_list, name="contract_list"),
    path("contracts/new/", views.contract_create, name="contract_create"),
    path("contracts/<int:pk>/", views.contract_detail, name="contract_detail"),
    path("contracts/<int:pk>/files/upload/", views.contract_file_upload, name="contract_file_upload"),
    path("contracts/<int:pk>/edit/", views.contract_update, name="contract_update"),
    path("contracts/<int:pk>/delete/", views.contract_delete, name="contract_delete"),
    path("contracts/<int:pk>/records/new/", views.record_add, name="record_add"),
    path("contracts/<int:pk>/invoice-records/new/", views.invoice_record_create, name="invoice_record_create"),
    path("contracts/<int:pk>/payment-records/new/", views.payment_record_create, name="payment_record_create"),
]
