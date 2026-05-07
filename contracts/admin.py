from django.contrib import admin

from .models import AppSetting, Contract, ContractFile, InvoiceRecord, PaymentRecord


# 注册合同模型到 Django 管理后台。
@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    # 后台列表页显示的字段。
    list_display = (
        "contract_name",
        "contract_number",
        "contract_type",
        "party_name",
        "amount",
        "invoice_status",
        "end_date",
    )
    # 后台搜索支持合同名称、编号和甲方名称。
    search_fields = ("contract_name", "contract_number", "party_name")
    # 后台右侧筛选项。
    list_filter = ("contract_type", "invoice_status", "end_date")


# 注册合同附件模型到后台。
@admin.register(ContractFile)
class ContractFileAdmin(admin.ModelAdmin):
    # 后台附件列表显示所属合同、原文件名和上传时间。
    list_display = ("contract", "original_name", "created_at")
    # 后台附件搜索支持合同信息和文件名。
    search_fields = ("contract__contract_name", "contract__contract_number", "original_name")


# 注册开票记录模型到后台。
@admin.register(InvoiceRecord)
class InvoiceRecordAdmin(admin.ModelAdmin):
    # 后台开票记录列表显示的字段。
    list_display = ("contract", "record_date", "amount", "remark")
    # 后台开票记录搜索字段。
    search_fields = ("contract__contract_name", "contract__contract_number", "remark")
    # 后台按日期筛选开票记录。
    list_filter = ("record_date",)


# 注册收票记录模型到后台。
@admin.register(PaymentRecord)
class PaymentRecordAdmin(admin.ModelAdmin):
    # 后台收票记录列表显示的字段。
    list_display = ("contract", "record_date", "amount", "remark")
    # 后台收票记录搜索字段。
    search_fields = ("contract__contract_name", "contract__contract_number", "remark")
    # 后台按日期筛选收票记录。
    list_filter = ("record_date",)


# 注册系统设置模型到后台。
@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    # 后台显示文件上传相关开关和更新时间。
    list_display = ("delete_source_file", "updated_at")
