import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="uploads/%Y/%m/")),
                ("file_type", models.CharField(default="image", max_length=20)),
                ("mime_type", models.CharField(blank=True, max_length=100)),
                ("width", models.PositiveIntegerField(blank=True, null=True)),
                ("height", models.PositiveIntegerField(blank=True, null=True)),
                ("size_bytes", models.PositiveBigIntegerField(default=0)),
                ("alt_text", models.CharField(blank=True, max_length=300)),
                ("caption", models.CharField(blank=True, max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL
                    ),
                ),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
    ]
