from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0003_app_setting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsetting",
            name="delete_source_file",
            field=models.BooleanField(default=False, verbose_name="删除被替换或删除的已上传文件"),
        ),
    ]
