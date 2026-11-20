from django.conf import settings
from django.db import models


class MediaAsset(models.Model):
    """One uploaded image in the reusable library. Stories reference it by id, never by URL."""

    file = models.FileField(upload_to="uploads/%Y/%m/")
    file_type = models.CharField(max_length=20, default="image")
    mime_type = models.CharField(max_length=100, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    # The uploader's file name, cleaned, for display only. The stored file name is always a random uuid.
    original_name = models.CharField(max_length=120, blank=True)
    alt_text = models.CharField(max_length=300, blank=True)
    caption = models.CharField(max_length=500, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.file.name
