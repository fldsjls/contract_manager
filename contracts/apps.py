from django.apps import AppConfig


# 合同应用的基础配置。
# 类说明：封装相关数据和行为。
class ContractsConfig(AppConfig):
    # 设置模型默认主键类型。
    default_auto_field = "django.db.models.BigAutoField"
    # Django 应用包名。
    name = "contracts"
    # 后台中显示的应用中文名。
    verbose_name = "合同管理"

    def ready(self):
        import reversion

        from .models import (
            AppSetting,
            Contract,
            ContractFile,
            InvoiceRecord,
            InvoiceRecordFileVersion,
            MaintenanceRecord,
            MaintenanceRecordFileVersion,
            PaymentRecord,
            PaymentRecordFileVersion,
            SettlementFile,
        )

        for model in (
            Contract,
            ContractFile,
            SettlementFile,
            InvoiceRecord,
            PaymentRecord,
            MaintenanceRecord,
            InvoiceRecordFileVersion,
            PaymentRecordFileVersion,
            MaintenanceRecordFileVersion,
            AppSetting,
        ):
            if not reversion.is_registered(model):
                reversion.register(model)
