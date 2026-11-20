from rest_framework import serializers

from apps.accounts.utils import display_name
from apps.core.text import clean_plain_text

from .models import MediaAsset
from .services import asset_usage


class MediaAssetSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()
    uploaded_by = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = [
            "id", "url", "file_type", "mime_type", "width", "height", "size_bytes",
            "alt_text", "caption", "original_name", "uploaded_by", "created_at",
        ]

    def get_url(self, obj):
        return obj.file.url if obj.file else None  # relative on local disk, absolute with object storage

    def get_uploaded_by(self, obj):
        return display_name(obj.uploaded_by)


class MediaAssetDetailSerializer(MediaAssetSerializer):
    used_in = serializers.SerializerMethodField()

    class Meta(MediaAssetSerializer.Meta):
        fields = MediaAssetSerializer.Meta.fields + ["used_in"]

    def get_used_in(self, obj):
        return asset_usage(obj)


class MediaAssetUpdateSerializer(serializers.ModelSerializer):
    """Only the descriptive text may change. Everything else about an asset is derived from the file."""

    class Meta:
        model = MediaAsset
        fields = ["alt_text", "caption"]

    # Plain text only (shown as text everywhere): control characters out, "<" and ">" refused, length limited.
    def validate_alt_text(self, value):
        return clean_plain_text(value, max_length=300)

    def validate_caption(self, value):
        return clean_plain_text(value, max_length=500)


class MediaCoverSerializer(serializers.ModelSerializer):
    """Compact form embedded in stories as `cover`."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = MediaAsset
        fields = ["id", "url", "alt_text", "width", "height"]

    def get_url(self, obj):
        return obj.file.url if obj.file else None
