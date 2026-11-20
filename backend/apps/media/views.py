import time

from django.core.cache import cache
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers, status
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit import service as audit
from apps.core.pagination import StandardPagination
from apps.core.permissions import IsStaffUser
from apps.core.text import clean_plain_text
from django.conf import settings

from .images import process_upload, process_uploads
from .models import MediaAsset
from .serializers import MediaAssetDetailSerializer, MediaAssetSerializer, MediaAssetUpdateSerializer
from .services import asset_usage

ORDERINGS = {"created_at", "-created_at", "size_bytes", "-size_bytes"}


class GalleryPagination(StandardPagination):
    page_size = 24


def consume_upload_quota(user, count):
    """At most MEDIA_UPLOAD_QUOTA_PER_HOUR files per user per hour (in addition to the per-request throttle).
    Counted in the cache (Redis in production) in one-hour windows."""
    limit = settings.MEDIA_UPLOAD_QUOTA_PER_HOUR
    window = int(time.time() // 3600)
    key = f"media-quota:{user.pk}:{window}"
    cache.add(key, 0, timeout=3700)
    used = cache.get(key, 0)
    if used + count > limit:
        wait = 3600 - int(time.time() % 3600)
        raise Throttled(wait=wait, detail=f"Upload limit reached ({limit} images per hour). Try again later.")
    try:
        cache.incr(key, count)
    except ValueError:  # the key expired between add and incr
        cache.set(key, count, timeout=3700)


class AdminMediaListCreateView(generics.ListCreateAPIView):
    permission_classes = [IsStaffUser]
    serializer_class = MediaAssetSerializer
    pagination_class = GalleryPagination

    @property
    def throttle_scope(self):
        return "media_upload" if self.request.method == "POST" else None  # uploads only

    def get_queryset(self):
        qs = MediaAsset.objects.select_related("uploaded_by__profile")
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(alt_text__icontains=q) | qs.filter(caption__icontains=q)
        ordering = self.request.query_params.get("ordering")
        return qs.order_by(ordering, "-id") if ordering in ORDERINGS else qs.order_by("-created_at", "-id")

    def create(self, request, *args, **kwargs):
        files = request.FILES.getlist("files") + request.FILES.getlist("file")
        if not files:
            raise serializers.ValidationError({"file": ["No file was uploaded."]})
        if len(files) > settings.MEDIA_MAX_FILES_PER_REQUEST:
            raise serializers.ValidationError({"file": [f"Upload at most {settings.MEDIA_MAX_FILES_PER_REQUEST} images at a time."]})

        processed = process_uploads(files)  # all-or-nothing: nothing is stored unless every file is valid
        alt_text = clean_plain_text(request.data.get("alt_text", ""), max_length=300) if len(files) == 1 else ""
        consume_upload_quota(request.user, len(files))

        created = []
        try:
            for content, meta in processed:
                asset = MediaAsset(uploaded_by=request.user, alt_text=alt_text, **meta)
                try:
                    asset.file.save(content.name, content, save=True)
                except Exception:
                    if asset.file and asset.file.name:
                        asset.file.storage.delete(asset.file.name)
                    raise
                created.append(asset)
        except Exception:
            for asset in created:  # storing failed halfway: leave nothing behind
                asset.file.delete(save=False)
                asset.delete()
            raise
        for asset in created:
            audit.log(request, "media.upload", asset, size_bytes=asset.size_bytes, mime_type=asset.mime_type)
        return Response(MediaAssetSerializer(created, many=True).data, status=status.HTTP_201_CREATED)


class AdminMediaDetailView(generics.RetrieveUpdateDestroyAPIView):
    permission_classes = [IsStaffUser]
    serializer_class = MediaAssetDetailSerializer
    http_method_names = ["get", "patch", "delete", "head", "options"]
    queryset = MediaAsset.objects.select_related("uploaded_by__profile")

    def update(self, request, *args, **kwargs):
        asset = self.get_object()
        serializer = MediaAssetUpdateSerializer(asset, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()  # other fields in the body are ignored
        audit.log(request, "media.update", asset, fields=sorted(serializer.validated_data))
        return Response(MediaAssetDetailSerializer(asset).data)

    def destroy(self, request, *args, **kwargs):
        asset = self.get_object()
        used_in = asset_usage(asset)
        if used_in and request.query_params.get("force") != "1":
            # Built by hand (not a ValidationError) so ids stay numbers instead of being turned into strings.
            message = f"This image is used in {len(used_in)} place(s). Remove it there first, or delete it anyway with ?force=1."
            return Response({"errors": {"detail": [message], "used_in": used_in}, "message": message}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():  # the audit entry is rolled back if removing the file or the row fails
            audit.log(request, "media.delete", asset, used_in_count=len(used_in), forced=bool(used_in))
            asset.file.delete(save=False)  # remove the stored file; story covers become null (SET_NULL)
            asset.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminMediaReplaceView(APIView):
    """Swap the file behind an existing asset. The id stays the same, so stories keep working."""

    permission_classes = [IsStaffUser]
    throttle_scope = "media_upload"

    def post(self, request, pk, *args, **kwargs):
        asset = get_object_or_404(MediaAsset, pk=pk)
        upload = request.FILES.get("file")
        if upload is None:
            raise serializers.ValidationError({"file": ["No file was uploaded."]})
        content, meta = process_upload(upload)
        consume_upload_quota(request.user, 1)

        old_name = asset.file.name
        asset.file.save(content.name, content, save=False)
        new_name = asset.file.name
        for field, value in meta.items():
            setattr(asset, field, value)
        try:
            asset.save()
        except Exception:
            asset.file.storage.delete(new_name)
            raise
        if old_name and old_name != new_name:
            asset.file.storage.delete(old_name)
        audit.log(request, "media.replace", asset, size_bytes=asset.size_bytes, mime_type=asset.mime_type)
        return Response(MediaAssetDetailSerializer(asset).data)
