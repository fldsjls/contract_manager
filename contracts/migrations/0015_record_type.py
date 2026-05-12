from django.db import migrations, models


# 给发票/收据记录增加“类型”字段，用于区分开票、收票、开据和收据。
class Migration(migrations.Migration):

    # 类型字段建立在结算文件迁移之后，避免迁移顺序交叉。
    dependencies = [
        ("contracts", "0014_settlementfile"),
    ]

    # 允许为空以兼容已有历史记录，统计时会有兜底分类逻辑。
    operations = [
        migrations.AddField(
            model_name="invoicerecord",
            name="record_type",
            field=models.CharField(blank=True, max_length=20, verbose_name="类型"),
        ),
        migrations.AddField(
            model_name="paymentrecord",
            name="record_type",
            field=models.CharField(blank=True, max_length=20, verbose_name="类型"),
        ),
    ]
