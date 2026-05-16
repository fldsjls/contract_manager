from django.contrib import admin
from django.urls import re_path
from reversion.admin import VersionAdmin

from .models import (
    AppSetting,
    Contract,
    ContractFile,
    InvoiceRecord,
    InvoiceRecordFileVersion,
    MaintenanceRecord,
    MaintenanceRecordFileVersion,
    MaintenanceRecordVolumeSequence,
    OperationLog,
    PaymentRecord,
    PaymentRecordFileVersion,
    SettlementFile,
)


# 复用 reversion 历史页，同时隐藏默认恢复入口。
class HistoryOnlyVersionAdmin(VersionAdmin):
    change_list_template = "admin/change_list.html"

    # 只保留历史详情 URL，避免后台暴露不符合业务流程的恢复操作。
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
        "storage_location_number",
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


# 后台类：查看项目记录分册和实序编号的对应关系。
@admin.register(MaintenanceRecordVolumeSequence)
class MaintenanceRecordVolumeSequenceAdmin(HistoryOnlyVersionAdmin):
    list_display = ("contract", "storage_location_number", "real_sequence_number", "shelf_position_number", "is_reserved", "updated_at")
    search_fields = ("contract__contract_name", "contract__contract_number", "storage_location_number")
    list_filter = ("storage_location_number", "shelf_position_number", "is_reserved")


# 后台类：查看开票记录附件的历史版本。
@admin.register(InvoiceRecordFileVersion)
class InvoiceRecordFileVersionAdmin(HistoryOnlyVersionAdmin):
    list_display = ("record", "original_name", "created_at")
    search_fields = ("record__contract__contract_name", "record__contract__contract_number", "original_name")
    list_filter = ("created_at",)


# 后台类：查看收票记录附件的历史版本。
@admin.register(PaymentRecordFileVersion)
class PaymentRecordFileVersionAdmin(HistoryOnlyVersionAdmin):
    list_display = ("record", "original_name", "created_at")
    search_fields = ("record__contract__contract_name", "record__contract__contract_number", "original_name")
    list_filter = ("created_at",)


# 后台类：查看项目记录附件的历史版本。
@admin.register(MaintenanceRecordFileVersion)
class MaintenanceRecordFileVersionAdmin(HistoryOnlyVersionAdmin):
    list_display = ("record", "original_name", "created_at")
    search_fields = ("record__contract__contract_name", "record__contract__contract_number", "original_name")
    list_filter = ("created_at",)


# 注册系统设置模型到后台。
# 后台类：配置模型在 Django 管理后台的显示和筛选。
@admin.register(AppSetting)
class AppSettingAdmin(HistoryOnlyVersionAdmin):
    # 后台显示系统设置和更新时间。
    list_display = ("allow_partial_import_with_errors", "image_root_path", "updated_at")


# 后台类：查看系统操作日志，禁止人工新增或删除。
@admin.register(OperationLog)
class OperationLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "username",
        "role",
        "action",
        "object_type",
        "object_name",
        "is_undone",
        "ip_address",
    )
    list_filter = ("action", "role", "object_type", "is_undone", "created_at")
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
        "detail",
        "ip_address",
        "created_at",
        "is_undone",
        "undone_at",
    )

    # 操作日志只能由系统写入，后台不允许手工新增。
    def has_add_permission(self, request):
        return False

    # 操作日志作为审计数据保留，后台不允许手工删除。
    def has_delete_permission(self, request, obj=None):
        return False
