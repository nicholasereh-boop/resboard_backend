import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("media", "0002_original_name"),
        ("news", "0006_article_archived_status"),
    ]

    operations = [
        migrations.AddField(model_name="newsarticle", name="content_json", field=models.JSONField(blank=True, null=True)),
        migrations.AddField(
            model_name="newsarticle", name="cover_image",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to="media.mediaasset"),
        ),
        migrations.AddField(model_name="newsarticle", name="reading_minutes", field=models.PositiveSmallIntegerField(default=0)),
    ]
