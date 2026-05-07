# 将合同、开票和收票附件保存路径统一到项目名称文件夹。
import contracts.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0008_alter_contract_contract_type_default"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contract",
            name="file",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=contracts.models.project_file_upload_path,
                verbose_name="合同文件",
            ),
        ),
        migrations.AlterField(
            model_name="contractfile",
            name="file",
            field=models.FileField(
                upload_to=contracts.models.project_file_upload_path,
                verbose_name="合同文件",
            ),
        ),
        migrations.AlterField(
            model_name="invoicerecord",
            name="file",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=contracts.models.project_file_upload_path,
                verbose_name="附件",
            ),
        ),
        migrations.AlterField(
            model_name="paymentrecord",
            name="file",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to=contracts.models.project_file_upload_path,
                verbose_name="附件",
            ),
        ),
    ]
