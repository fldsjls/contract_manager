from django.contrib import admin
from django.urls import re_path
from django.utils.html import format_html
from reversion.admin import VersionAdmin

from .models import AppSetting, Contract, ContractFile, InvoiceRecord, MaintenanceRecord, OperationLog, PaymentRecord, SettlementFile


class HistoryOnlyVersionAdmin(VersionAdmin):
    change_list_template = "admin/change_list.html"

    def get_urls(self):
        urls = admin.ModelAdmin.get_urls(self)
        admin_site = self.admin_site
        opts = self.model._meta
        info = opts.app_label, opts.model_name
        return [
            re_path(
                r"^([^/]+)/history/(\d+)/$",
                admin_site.admin_view(self.revision_view),
                name="%s_%s_revision" % info,
            ),
        ] + urls


# 注册合同模型到 Django 管理后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(Contract)
class ContractAdmin(VersionAdmin):
    # 后台列表页显示的字段。
    list_display = (
        "contract_name",
        "display_contract_number",
        "contract_type",
        "party_name",
        "amount",
        "invoice_status",
        "end_date",
        "is_deleted",
        "deleted_at",
    )
    # 后台搜索支持合同名称、编号和甲方名称。
    search_fields = (
        "contract_name",
        "contract_number",
        "original_contract_folder",
        "original_contract_inner_number",
        "party_name",
    )
    # 后台右侧筛选项。
    list_filter = ("contract_type", "invoice_status", "is_deleted", "end_date")

    # 方法说明：在后台列表中显示组合后的合同编号。
    def display_contract_number(self, obj):
        return obj.display_contract_number

    display_contract_number.short_description = "合同编号"


# 注册合同附件模型到后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(ContractFile)
class ContractFileAdmin(HistoryOnlyVersionAdmin):
    # 后台附件列表显示所属合同、原文件名和上传时间。
    list_display = ("contract", "original_name", "sort_order", "created_at")
    # 后台附件搜索支持合同信息和文件名。
    search_fields = ("contract__contract_name", "contract__contract_number", "original_name")


# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(SettlementFile)
class SettlementFileAdmin(HistoryOnlyVersionAdmin):
    list_display = ("contract", "original_name", "created_at")
    search_fields = ("contract__contract_name", "contract__contract_number", "original_name")
    list_filter = ("created_at",)


# 注册开票记录模型到后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(InvoiceRecord)
class InvoiceRecordAdmin(HistoryOnlyVersionAdmin):
    # 后台开票记录列表显示的字段。
    list_display = ("contract", "record_date", "record_type", "amount", "actual_amount", "remark")
    # 后台开票记录搜索字段。
    search_fields = ("contract__contract_name", "contract__contract_number", "remark")
    # 后台按日期筛选开票记录。
    list_filter = ("record_date",)


# 注册收票记录模型到后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(PaymentRecord)
class PaymentRecordAdmin(HistoryOnlyVersionAdmin):
    # 后台收票记录列表显示的字段。
    list_display = ("contract", "record_date", "record_type", "amount", "actual_amount", "remark")
    # 后台收票记录搜索字段。
    search_fields = ("contract__contract_name", "contract__contract_number", "remark")
    # 后台按日期筛选收票记录。
    list_filter = ("record_date",)


# 注册维护保养记录模型到后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(MaintenanceRecord)
class MaintenanceRecordAdmin(HistoryOnlyVersionAdmin):
    # 后台维护保养记录列表显示的字段。
    list_display = ("contract", "record_date", "month", "remark")
    # 后台维护保养记录搜索字段。
    search_fields = ("contract__contract_name", "contract__contract_number", "month", "remark")
    # 后台按日期筛选维护保养记录。
    list_filter = ("record_date",)


# 注册系统设置模型到后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(AppSetting)
class AppSettingAdmin(HistoryOnlyVersionAdmin):
    # 后台显示文件上传相关开关和更新时间。
    list_display = ("delete_source_file", "image_root_path", "updated_at")


@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "username",
        "role",
        "action",
        "object_type",
        "object_name",
        "ip_address",
        "object_history_link",
    )
    list_filter = ("action", "role", "object_type", "created_at")
    search_fields = ("username", "object_name", "detail", "ip_address")
    readonly_fields = (
        "user",
        "username",
        "role",
        "action",
        "object_type",
        "object_name",
        "object_id",
        "content_type",
        "object_pk",
        "object_history_link",
        "detail",
        "ip_address",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def object_history_link(self, obj):
        if obj and obj.history_url:
            return format_html(
                '<a class="button" style="white-space: nowrap;" href="{}">查看对象历史</a>',
                obj.history_url,
            )
        return "-"

    object_history_link.short_description = "对象历史"
