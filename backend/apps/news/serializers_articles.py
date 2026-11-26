"""Staff and public serializers for articles, categories and assets. Public ones never include drafts, hashes, reviewer notes or evidence."""
from rest_framework import serializers

from apps.core.text import clean_plain_text
from apps.media.models import MediaAsset
from apps.stories.content import asset_ids, clean_content, resolve_media  # the same document schema and media library the stories use

from .models import NewsAsset, NewsCategory, NewsSource
from .services import policy, rights, sanitize
from .services.articles import check_publishable


class CategoryWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsCategory
        fields = ["name", "slug", "description", "position", "is_active"]

    def validate_name(self, value):
        return clean_plain_text(value, max_length=80, allow_blank=False)

    def validate_description(self, value):
        return clean_plain_text(value, max_length=300)


class ArticleWriteSerializer(serializers.Serializer):
    """Titles and names are plain text (React escapes them); only the body is rich text, and it is sanitised in the service."""

    source = serializers.PrimaryKeyRelatedField(queryset=NewsSource.objects.all(), required=False)
    title = serializers.CharField(max_length=220)
    standfirst = serializers.CharField(max_length=500, required=False, allow_blank=True)
    body = serializers.CharField(required=False, allow_blank=True, max_length=400_000)
    author_name = serializers.CharField(max_length=160, required=False, allow_blank=True)
    category = serializers.PrimaryKeyRelatedField(queryset=NewsCategory.objects.all(), required=False, allow_null=True)
    language = serializers.RegexField(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$", required=False, max_length=8)
    is_featured = serializers.BooleanField(required=False)
    is_breaking = serializers.BooleanField(required=False)
    canonical_url = serializers.URLField(max_length=700, required=False, allow_blank=True)
    original_url = serializers.URLField(max_length=700, required=False, allow_blank=True)
    source_published_at = serializers.DateTimeField(required=False, allow_null=True)
    note = serializers.CharField(max_length=300, required=False, allow_blank=True)
    content_json = serializers.JSONField(required=False)
    cover_image = serializers.PrimaryKeyRelatedField(
        queryset=MediaAsset.objects.all(), allow_null=True, required=False,
        error_messages={"does_not_exist": "That image does not exist.", "incorrect_type": "That image does not exist."},
    )

    def validate_content_json(self, value):
        cleaned = clean_content(value)  # rebuilt from an allowlist: the only way a document reaches the database
        ids = asset_ids(cleaned)
        if ids:
            missing = sorted(ids - set(MediaAsset.objects.filter(pk__in=ids).values_list("pk", flat=True)))
            if missing:
                raise serializers.ValidationError("These images are not in the media library: " + ", ".join(map(str, missing)) + ".")
        return cleaned

    def validate_title(self, value):
        return clean_plain_text(value, max_length=220, allow_blank=False)

    def validate_standfirst(self, value):
        return clean_plain_text(value, max_length=500)

    def validate_author_name(self, value):
        return clean_plain_text(value, max_length=160)

    def validate(self, attrs):
        for key in ("canonical_url", "original_url"):
            if attrs.get(key) and not attrs[key].lower().startswith("https://"):
                raise serializers.ValidationError({key: "Use an https address."})
        return attrs


class ArticleUpdateSerializer(ArticleWriteSerializer):
    title = serializers.CharField(max_length=220, required=False)

    def validate_title(self, value):
        return clean_plain_text(value, max_length=220, allow_blank=False)


class AssetWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = NewsAsset
        fields = ["kind", "image", "source_url", "video_id", "alt_text", "caption", "creator", "credit", "licence", "allows_commercial_use",
                  "allows_modification", "evidence_url", "expires_at", "position"]

    def validate(self, attrs):
        kind = attrs.get("kind", getattr(self.instance, "kind", "image"))
        if kind == "video":
            vid = attrs.get("video_id", getattr(self.instance, "video_id", ""))
            if len(vid) != 11:
                raise serializers.ValidationError({"video_id": "Enter the 11-character YouTube video id."})
        elif not (attrs.get("image") or getattr(self.instance, "image_id", None)):
            raise serializers.ValidationError({"image": "Choose an image from the media library."})
        for key in ("alt_text", "caption", "creator", "credit", "licence"):
            if key in attrs:
                attrs[key] = clean_plain_text(attrs[key], max_length=self.fields[key].max_length)
        return attrs


def cover_data(asset):
    if asset is None or not asset.file:
        return None
    return {"id": asset.pk, "url": asset.file.url, "alt_text": asset.alt_text, "width": asset.width, "height": asset.height}


def category_data(c):
    return {"id": c.pk, "name": c.name, "slug": c.slug, "description": c.description, "position": c.position, "is_active": c.is_active}


def asset_admin_data(a):
    return {
        "id": a.pk, "article": a.article_id, "kind": a.kind, "image": a.image_id, "image_url": a.image.file.url if a.image_id and a.image.file else "",
        "source_url": a.source_url, "video_id": a.video_id, "alt_text": a.alt_text, "caption": a.caption, "creator": a.creator, "credit": a.credit,
        "licence": a.licence, "allows_commercial_use": a.allows_commercial_use, "allows_modification": a.allows_modification,
        "evidence_url": a.evidence_url, "expires_at": a.expires_at, "approval_state": a.approval_state, "displayable": a.is_displayable(), "position": a.position,
    }


def article_admin_data(a, *, full=False):
    data = {
        "id": a.pk, "slug": a.slug, "title": a.title, "standfirst": a.standfirst, "status": a.status, "article_type": a.article_type,
        "source": {"id": a.source_id, "name": a.source.name, "source_type": a.source.source_type}, "category": category_data(a.category) if a.category_id else None,
        "author_name": a.author_name, "is_featured": a.is_featured, "is_breaking": a.is_breaking, "imported": a.imported,
        "published_at": a.published_at, "scheduled_for": a.scheduled_for, "quarantine_reason": a.quarantine_reason, "cover": cover_data(a.cover_image),
        "reading_minutes": a.reading_minutes,
        "review_note": a.review_note, "created_at": a.created_at, "updated_at": a.updated_at,
    }
    if full:
        ok, reasons = check_publishable(a)
        data.update({
            "body": a.body, "content_json": resolve_media(a.content_json) if a.content_json else None, "language": a.language, "attribution": a.attribution, "canonical_url": a.canonical_url, "original_url": a.original_url,
            "external_id": a.external_id, "source_published_at": a.source_published_at, "rights_record": a.rights_record_id,
            "can_publish": ok, "blockers": reasons, "assets": [asset_admin_data(x) for x in a.assets.all()],
            "denylisted_publisher": policy.source_is_denylisted(a.source), "decision": rights.evaluate(a.source, record=a.rights_record).as_dict() if a.rights_record_id else None,
        })
    return data


def revision_data(r):
    return {"id": r.pk, "number": r.number, "action": r.action, "title": r.title, "note": r.note, "user": r.user.email if r.user_id else "system", "created_at": r.created_at}


# ---- public ------------------------------------------------------------------------------------
def _image(asset):
    return {"url": asset.image.file.url, "width": asset.image.width, "height": asset.image.height} if asset.image_id and asset.image.file else None


def public_asset(a):
    return {"id": a.pk, "kind": a.kind, "image": _image(a), "video_id": a.video_id, "alt": a.alt_text, "caption": a.caption, "creator": a.creator, "credit": a.credit, "licence": a.licence}


def public_card(a):
    lead = next((x for x in a.assets.all() if x.kind == "image" and x.is_displayable()), None)
    return {
        "slug": a.slug, "title": a.title, "standfirst": a.standfirst, "author_name": a.author_name, "published_at": a.published_at,
        "category": {"name": a.category.name, "slug": a.category.slug} if a.category_id else None,
        "source": {"name": a.source.name, "slug": a.source.slug}, "is_featured": a.is_featured, "is_breaking": a.is_breaking,
        "article_type": a.article_type, "lead_image": {**_image(lead), "alt": lead.alt_text, "credit": lead.credit} if lead and _image(lead) else None,
        # Story-shaped fields, so the story cards and the news cards can share one design:
        "cover": cover_data(a.cover_image) or ({"url": _image(lead)["url"], "alt_text": lead.alt_text, "width": _image(lead)["width"], "height": _image(lead)["height"]} if lead and _image(lead) else None),
        "author": a.author_name, "excerpt": a.standfirst, "reading_minutes": a.reading_minutes, "path": f"/news/{a.slug}",
    }


def public_detail(a, decision):
    show_original = decision.attribution_required and (a.original_url or a.canonical_url)
    return {
        **public_card(a),
        "body": a.body, "content_json": resolve_media(a.content_json) if a.content_json else None, "language": a.language, "attribution": a.attribution, "source_published_at": a.source_published_at,
        "original_url": (a.original_url or a.canonical_url) if show_original else "",
        "counter_required": decision.counter_required,
        "licence": decision.record.licence if decision.record else "",
        "assets": [public_asset(x) for x in a.assets.all() if x.is_displayable()],
        "word_count": len(sanitize.plain_text(a.body).split()),
    }
