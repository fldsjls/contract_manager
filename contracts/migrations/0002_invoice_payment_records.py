import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="InvoiceRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("record_date", models.DateField(verbose_name="日期")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="金额")),
                ("file", models.FileField(blank=True, null=True, upload_to="records/", verbose_name="附件")),
                ("remark", models.CharField(blank=True, max_length=255, verbose_name="备注")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="contracts.contract",
                        verbose_name="所属合同",
                    ),
                ),
            ],
            options={
                "verbose_name": "开票记录",
                "verbose_name_plural": "开票记录",
                "ordering": ["record_date", "id"],
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="PaymentRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("record_date", models.DateField(verbose_name="日期")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="金额")),
                ("file", models.FileField(blank=True, null=True, upload_to="records/", verbose_name="附件")),
                ("remark", models.CharField(blank=True, max_length=255, verbose_name="备注")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="contracts.contract",
                        verbose_name="所属合同",
                    ),
                ),
            ],
            options={
                "verbose_name": "收票记录",
                "verbose_name_plural": "收票记录",
                "ordering": ["record_date", "id"],
                "abstract": False,
            },
        ),
    ]
