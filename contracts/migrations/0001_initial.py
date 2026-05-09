# Generated for the Django LAN contract system.

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Contract",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("contract_name", models.CharField(max_length=200, verbose_name="合同名称")),
                ("contract_number", models.CharField(max_length=50, unique=True, verbose_name="合同编号")),
                (
                    "contract_type",
                    models.CharField(
                        choices=[
                            ("维保", "维保"),
                            ("评估", "评估"),
                            ("检测", "检测"),
                            ("改造", "改造"),
                            ("新建", "新建"),
                            ("其他项目", "其他项目"),
                        ],
                        default="其他项目",
                        max_length=20,
                        verbose_name="合同类型",
                    ),
                ),
                ("party_name", models.CharField(max_length=200, verbose_name="甲方名称")),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=14, verbose_name="合同金额")),
                (
                    "invoice_status",
                    models.CharField(
                        choices=[("不开票", "不开票"), ("开票", "开票")],
                        default="不开票",
                        max_length=20,
                        verbose_name="是否开票",
                    ),
                ),
                ("sign_date", models.DateField(blank=True, null=True, verbose_name="签订日期")),
                ("start_date", models.DateField(blank=True, null=True, verbose_name="开始日期")),
                ("end_date", models.DateField(blank=True, null=True, verbose_name="截止日期")),
                ("file", models.FileField(blank=True, null=True, upload_to="contracts/", verbose_name="合同文件")),
                ("remark", models.TextField(blank=True, verbose_name="备注")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="创建时间")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="更新时间")),
            ],
            options={
                "verbose_name": "合同",
                "verbose_name_plural": "合同",
                "ordering": ["end_date", "-id"],
            },
        ),
    ]
