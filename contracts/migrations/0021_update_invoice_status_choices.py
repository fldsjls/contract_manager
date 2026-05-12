from django.db import migrations, models


def forward_invoice_status(apps, _schema_editor):
    Contract = apps.get_model("contracts", "Contract")
    Contract.objects.filter(invoice_status="不开票").update(invoice_status="开收据")
    Contract.objects.filter(invoice_status="开票").update(invoice_status="待开票")


def backward_invoice_status(apps, _schema_editor):
    Contract = apps.get_model("contracts", "Contract")
    Contract.objects.filter(invoice_status="开收据").update(invoice_status="不开票")
    Contract.objects.filter(invoice_status__in=["待开票", "票已结"]).update(invoice_status="开票")


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0020_contract_original_contract_folder_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contract",
            name="invoice_status",
            field=models.CharField(
                choices=[("开收据", "开收据"), ("待开票", "待开票"), ("票已结", "票已结")],
                default="开收据",
                max_length=20,
                verbose_name="是否开票",
            ),
        ),
        migrations.RunPython(forward_invoice_status, backward_invoice_status),
    ]
