from django.contrib import admin

from .models import AppSetting, Contract, InvoiceRecord, PaymentRecord


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


@admin.register(InvoiceRecord)
class InvoiceRecordAdmin(admin.ModelAdmin):
    list_display = ("contract", "record_date", "amount", "remark")
    search_fields = ("contract__contract_name", "contract__contract_number", "remark")
    list_filter = ("record_date",)


@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    list_display = ("contract", "record_date", "amount", "remark")
    search_fields = ("contract__contract_name", "contract__contract_number", "remark")
    list_filter = ("record_date",)


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    list_display = ("delete_source_file", "updated_at")
