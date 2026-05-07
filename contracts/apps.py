from django.apps import AppConfig


# 合同应用的基础配置。
class ContractsConfig(AppConfig):
    # 设置模型默认主键类型。
    default_auto_field = "django.db.models.BigAutoField"
    # Django 应用包名。
    name = "contracts"
    # 后台中显示的应用中文名。
    verbose_name = "合同管理"
