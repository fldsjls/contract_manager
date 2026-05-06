from django.contrib import admin

from .models import Contract


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        "contract_name",
        "contract_number",
        "contract_type",
        "party_name",
        "amount",
        "invoice_status",
        "end_date",
    )
    search_fields = ("contract_name", "contract_number", "party_name")
    list_filter = ("contract_type", "invoice_status", "end_date")
