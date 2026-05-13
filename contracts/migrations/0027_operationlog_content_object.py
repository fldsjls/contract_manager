from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contenttypes", "0002_remove_content_type_name"),
        ("contracts", "0026_operationlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="operationlog",
            name="content_type",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="contenttypes.contenttype",
                verbose_name="对象模型",
            ),
        ),
        migrations.AddField(
            model_name="operationlog",
            name="object_pk",
            field=models.CharField(blank=True, max_length=50, verbose_name="对象主键"),
        ),
    ]
