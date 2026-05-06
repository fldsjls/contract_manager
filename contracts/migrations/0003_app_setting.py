from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0002_invoice_payment_records"),
    ]

    operations = [
        migrations.CreateModel(
            name="AppSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("delete_source_file", models.BooleanField(default=False, verbose_name="删除被替换或删除的原文件")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "系统设置",
                "verbose_name_plural": "系统设置",
            },
        ),
    ]
