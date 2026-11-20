from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("media", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="mediaasset",
            name="original_name",
            field=models.CharField(blank=True, max_length=120),
        ),
    ]
