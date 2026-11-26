"""python manage.py recycle_news [--dry-run] : archive in-house news older than NEWS_INHOUSE_RETENTION_DAYS (60). Also runs daily via Celery."""
from django.conf import settings
from django.core.management.base import BaseCommand

from apps.news.services import articles


class Command(BaseCommand):
    help = "Archive in-house news articles that have been public for the retention period (default 60 days)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only count what would be archived.")

    def handle(self, *args, **options):
        count = articles.recycle_expired(dry_run=options["dry_run"])
        verb = "Would archive" if options["dry_run"] else "Archived"
        self.stdout.write(self.style.SUCCESS(f"{verb} {count} article(s) older than {settings.NEWS_INHOUSE_RETENTION_DAYS} days."))
