"""News: articles written by our own staff (no outside source) and the 60-day recycle."""
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.news.models import NewsArticle, NewsSource
from apps.news.services import articles
from apps.news.services.articles import ArticleError
from apps.news.services.inhouse import INHOUSE_SLUG, get_inhouse_source

from .news_helpers import ADMIN, API, make_live_article, make_record, make_source

pytestmark = pytest.mark.django_db

PAYLOAD = {"title": "Burkina Faso president", "standfirst": "Rebuilding", "body": "<p>Testing the site</p>", "author_name": "John Mike", "language": "en"}


@pytest.fixture
def public(settings):
    settings.NEWS_PUBLIC_ENABLED = True


def _write_and_publish(staff_client, **extra):
    created = staff_client.post(ADMIN + "articles/", {**PAYLOAD, **extra}, format="json")
    assert created.status_code == 201, created.data
    published = staff_client.post(f"{ADMIN}articles/{created.data['id']}/publish/")
    assert published.status_code == 200, published.data
    return created.data["id"]


def test_article_without_a_source_uses_the_in_house_source(staff_client):
    r = staff_client.post(ADMIN + "articles/", PAYLOAD, format="json")
    assert r.status_code == 201, r.data
    assert r.data["source"]["name"] == "ResourceBoard"
    assert r.data["article_type"] == "original" and r.data["status"] == "draft"
    assert r.data["can_publish"] is True and r.data["blockers"] == []
    assert NewsSource.objects.filter(slug=INHOUSE_SLUG).count() == 1


def test_in_house_source_is_created_once_and_never_re_enabled(staff):
    first = get_inhouse_source()
    assert get_inhouse_source().pk == first.pk and first.rights_records.count() == 1
    first.is_active = False
    first.save()
    assert get_inhouse_source().is_active is False  # a deliberate switch-off is respected


def test_in_house_article_publishes_and_is_public(staff_client, public):
    _write_and_publish(staff_client)
    listing = staff_client.get(API + "articles/")
    assert [a["title"] for a in listing.data["results"]] == ["Burkina Faso president"]


def test_a_chosen_source_still_works_as_before(staff_client, staff):
    source = make_source()
    make_record(source)
    r = staff_client.post(ADMIN + "articles/", {**PAYLOAD, "source": source.pk}, format="json")
    assert r.status_code == 201 and r.data["source"]["id"] == source.pk


def test_recycle_archives_in_house_news_after_60_days(staff_client, public):
    old_id, new_id = _write_and_publish(staff_client), _write_and_publish(staff_client, title="Fresh one")
    NewsArticle.objects.filter(pk=old_id).update(published_at=timezone.now() - timedelta(days=61))
    assert articles.recycle_expired(dry_run=True) == 1
    assert articles.recycle_expired() == 1
    assert NewsArticle.objects.get(pk=old_id).status == "archived"
    assert NewsArticle.objects.get(pk=new_id).status == "published"
    assert [a["title"] for a in staff_client.get(API + "articles/").data["results"]] == ["Fresh one"]  # cache was refreshed
    assert staff_client.get(API + f"articles/{NewsArticle.objects.get(pk=old_id).slug}/").status_code == 404
    assert NewsArticle.objects.get(pk=old_id).revisions.filter(action="archived").exists()


def test_recycle_leaves_the_60_day_boundary_and_other_sources_alone(staff_client, staff):
    inside = _write_and_publish(staff_client)
    NewsArticle.objects.filter(pk=inside).update(published_at=timezone.now() - timedelta(days=59))
    licensed = make_source()
    make_record(licensed)
    other = make_live_article(licensed, staff)
    NewsArticle.objects.filter(pk=other.pk).update(published_at=timezone.now() - timedelta(days=400))
    assert articles.recycle_expired() == 0
    assert NewsArticle.objects.get(pk=other.pk).status == "published"


def test_recycle_can_be_switched_off_and_command_runs(staff_client, settings):
    old = _write_and_publish(staff_client)
    NewsArticle.objects.filter(pk=old).update(published_at=timezone.now() - timedelta(days=90))
    settings.NEWS_INHOUSE_RETENTION_DAYS = 0
    assert articles.recycle_expired() == 0
    settings.NEWS_INHOUSE_RETENTION_DAYS = 60
    call_command("recycle_news")
    assert NewsArticle.objects.get(pk=old).status == "archived"


def test_archived_article_is_restorable_not_editable(staff_client, staff):
    old = _write_and_publish(staff_client)
    NewsArticle.objects.filter(pk=old).update(published_at=timezone.now() - timedelta(days=61))
    articles.recycle_expired()
    article = NewsArticle.objects.get(pk=old)
    with pytest.raises(ArticleError):
        articles.update_article(article, staff, {"title": "New"})
    restored = staff_client.post(f"{ADMIN}articles/{old}/restore/")
    assert restored.status_code == 200 and restored.data["status"] == "pending"
    again = staff_client.post(f"{ADMIN}articles/{old}/publish/")
    assert again.data["status"] == "published"
    assert articles.recycle_expired() == 0  # a new 60-day cycle has started
