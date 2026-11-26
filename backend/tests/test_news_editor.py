"""News: the story-style editor (document + cover image from the shared media library) and what the public pages get from it."""
import pytest
from django.core.files.base import ContentFile

from apps.media.models import MediaAsset

from .news_helpers import ADMIN, API

pytestmark = pytest.mark.django_db


@pytest.fixture
def public(settings):
    settings.NEWS_PUBLIC_ENABLED = True


@pytest.fixture
def asset():
    a = MediaAsset(file_type="image", mime_type="image/png", width=40, height=30, alt_text="A picture")
    a.file.save("pic.png", ContentFile(b"x"), save=True)
    return a


def _doc(asset_id=None):
    content = [
        {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Big news"}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "The council voted on Tuesday.", "marks": [{"type": "bold"}]}]},
    ]
    if asset_id:
        content.append({"type": "image", "attrs": {"assetId": asset_id, "align": "center", "size": "full"}})
    return {"type": "doc", "content": content}


def test_editor_document_becomes_the_body_and_publishes(staff_client, asset):
    r = staff_client.post(ADMIN + "articles/", {"title": "Council votes", "content_json": _doc(asset.pk), "cover_image": asset.pk, "author_name": "Ada"}, format="json")
    assert r.status_code == 201, r.data
    assert "<h2>Big news</h2>" in r.data["body"] and "<strong>The council voted on Tuesday.</strong>" in r.data["body"]
    assert "<img" not in r.data["body"]
    assert r.data["cover"]["id"] == asset.pk and r.data["reading_minutes"] >= 1
    image = next(n for n in r.data["content_json"]["content"] if n["type"] == "image")
    assert image["attrs"]["src"] and image["attrs"]["width"] == 40  # resolved from the shared media library
    assert r.data["can_publish"] is True
    assert staff_client.post(f"{ADMIN}articles/{r.data['id']}/publish/").status_code == 200


def test_public_pages_get_cover_document_and_story_shaped_fields(staff_client, public, asset):
    r = staff_client.post(ADMIN + "articles/", {"title": "Council votes", "standfirst": "Work starts", "content_json": _doc(asset.pk), "cover_image": asset.pk, "author_name": "Ada"}, format="json")
    staff_client.post(f"{ADMIN}articles/{r.data['id']}/publish/")
    listing = staff_client.get(API + "articles/").data
    card = listing["items"][0] if "items" in listing else listing["results"][0]
    assert card["cover"]["url"] and card["author"] == "Ada" and card["excerpt"] == "Work starts" and card["path"] == f"/news/{card['slug']}"
    detail = staff_client.get(API + f"articles/{card['slug']}/").data
    assert detail["content_json"]["type"] == "doc" and detail["cover"]["url"] and "body" in detail


def test_unknown_image_and_unsafe_content_are_refused(staff_client):
    bad_image = staff_client.post(ADMIN + "articles/", {"title": "x", "content_json": _doc(999999)}, format="json")
    assert bad_image.status_code == 400
    bad_cover = staff_client.post(ADMIN + "articles/", {"title": "x", "cover_image": 999999}, format="json")
    assert bad_cover.status_code == 400
    evil = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "hi", "marks": [{"type": "link", "attrs": {"href": "javascript:alert(1)"}}]}]}]}
    assert staff_client.post(ADMIN + "articles/", {"title": "x", "content_json": evil}, format="json").status_code == 400


def test_editing_the_document_updates_the_body(staff_client):
    r = staff_client.post(ADMIN + "articles/", {"title": "T", "content_json": _doc()}, format="json")
    new = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Rewritten"}]}]}
    p = staff_client.patch(f"{ADMIN}articles/{r.data['id']}/", {"content_json": new}, format="json")
    assert p.status_code == 200 and "Rewritten" in p.data["body"] and "Big news" not in p.data["body"]
