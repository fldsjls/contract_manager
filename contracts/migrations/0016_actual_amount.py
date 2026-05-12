from django.db import migrations, models


# 给发票/收据记录增加实际金额字段，票面金额和实际金额分开统计。
# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):

    # 实际金额依赖记录类型字段，统计逻辑会同时使用两者。
    dependencies = [
        ("contracts", "0015_record_type"),
    ]

    # 允许为空以兼容旧记录，未填写时页面和统计回退到票面金额。
    operations = [
        migrations.AddField(
            model_name="invoicerecord",
            name="actual_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, verbose_name="实际金额"),
        ),
        migrations.AddField(
            model_name="paymentrecord",
            name="actual_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, verbose_name="实际金额"),
        ),
    ]
