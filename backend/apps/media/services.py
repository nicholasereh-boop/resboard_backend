from apps.stories.content import asset_ids
from apps.stories.models import Story


def asset_usage(asset):
    """Where is this image used? One entry per (story, use): used_as is "cover" or "content"."""
    usage = []
    for story in Story.objects.filter(cover_image=asset).values("id", "title", "status"):
        usage.append({**story, "used_as": "cover"})
    # Content references live inside JSON, so scan stories one by one (admin-only, only id/title/status/content loaded).
    for story in Story.objects.only("id", "title", "status", "content_json").iterator(chunk_size=200):
        if asset.pk in asset_ids(story.content_json):
            usage.append({"id": story.id, "title": story.title, "status": story.status, "used_as": "content"})
    return sorted(usage, key=lambda u: (u["id"], u["used_as"]))
