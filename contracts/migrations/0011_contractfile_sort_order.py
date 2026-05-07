from django.db import migrations, models


# 给已有合同文件按上传时间生成初始排序。
def fill_sort_order(apps, schema_editor):
    ContractFile = apps.get_model("contracts", "ContractFile")
    contract_ids = ContractFile.objects.values_list("contract_id", flat=True).distinct()
    for contract_id in contract_ids:
        files = ContractFile.objects.filter(contract_id=contract_id).order_by("created_at", "id")
        for index, item in enumerate(files):
            item.sort_order = index
            item.save(update_fields=["sort_order"])


# 撤销迁移时不需要额外处理排序值。
def noop(apps, schema_editor):
    return None


# 为合同文件增加排序字段，用于编辑页拖拽排序。
class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0010_contract_trash_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="contractfile",
            name="sort_order",
            field=models.PositiveIntegerField(default=0, verbose_name="排序"),
        ),
        migrations.RunPython(fill_sort_order, noop),
    ]
