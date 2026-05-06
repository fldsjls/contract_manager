from django.urls import path

from . import views


app_name = "contracts"

urlpatterns = [
    path("", views.contract_list, name="contract_list"),
]
