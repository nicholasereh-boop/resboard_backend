"""Up to three optional description videos: one for the frontend, one for the backend, one for both."""
import pytest
from rest_framework.test import APIClient

from apps.source_marketplace.models import SourceProject

from .marketplace_helpers import MP, client_for, new_project, private_dirs, publish, seller, upload  # noqa: F401

pytestmark = pytest.mark.django_db
A, B, C = "dQw4w9WgXcQ", "9bZkp7q19f0", "kJQP7kiw5Fk"


def test_all_three_videos_are_optional(seller):
    p = new_project(client_for(seller))
    assert (p["youtube_id"], p["frontend_video_id"], p["backend_video_id"]) == ("", "", "")


def test_a_seller_can_add_the_three_videos_and_only_the_id_is_stored(seller):
    c = client_for(seller)
    p = new_project(c, youtube_url=f"https://youtu.be/{A}", frontend_video_url=f"https://www.youtube.com/watch?v={B}",
                    backend_video_url=f"https://www.youtube.com/watch?v={C}")
    assert (p["youtube_id"], p["frontend_video_id"], p["backend_video_id"]) == (A, B, C)
    row = SourceProject.objects.get(pk=p["id"])
    assert (row.youtube_id, row.frontend_video_id, row.backend_video_id) == (A, B, C)


def test_each_video_can_be_added_alone_and_removed_again(seller):
    c = client_for(seller)
    p = new_project(c, backend_video_url=f"https://youtu.be/{C}")
    assert p["backend_video_id"] == C and p["frontend_video_id"] == "" and p["youtube_id"] == ""
    r = c.patch(MP + f"projects/{p['id']}/", {"backend_video_url": ""}, format="json")
    assert r.status_code == 200 and r.json()["data"]["backend_video_id"] == ""


@pytest.mark.parametrize("field", ["youtube_url", "frontend_video_url", "backend_video_url"])
def test_a_link_that_is_not_youtube_is_refused(seller, field):
    r = client_for(seller).post(MP + "projects/", {"title": "T", field: "https://evil.example/video"}, format="json")
    assert r.status_code == 400 and field in r.json()["errors"]


def test_the_public_page_shows_the_videos_and_the_card_says_there_is_one(private_dirs, seller):
    c = client_for(seller)
    p = new_project(c, frontend_price="0", frontend_video_url=f"https://youtu.be/{B}")
    upload(c, p["id"], kind="frontend")
    publish(p["id"])
    page = APIClient().get(MP + f"projects/{p['slug']}/").json()["data"]
    assert page["frontend_video_id"] == B and page["backend_video_id"] == "" and page["has_video"] is True
