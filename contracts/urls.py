from django.urls import path

from . import views


app_name = "contracts"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("settings/", views.settings_view, name="settings"),
    path("contracts/", views.contract_list, name="contract_list"),
    path("contracts/new/", views.contract_create, name="contract_create"),
    path("contracts/<int:pk>/", views.contract_detail, name="contract_detail"),
    path("contracts/<int:pk>/edit/", views.contract_update, name="contract_update"),
    path("contracts/<int:pk>/delete/", views.contract_delete, name="contract_delete"),
    path("contracts/<int:pk>/records/new/", views.record_add, name="record_add"),
    path("contracts/<int:pk>/invoice-records/new/", views.invoice_record_create, name="invoice_record_create"),
    path("contracts/<int:pk>/payment-records/new/", views.payment_record_create, name="payment_record_create"),
]
