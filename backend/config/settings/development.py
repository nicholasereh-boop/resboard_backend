"""Local development. Uses PostgreSQL when configured, SQLite only if no DB variables are set."""
import os

from .base import *  # noqa: F401,F403

DEBUG = True
# A throwaway key so a fresh checkout starts; production refuses to start without a real one.
SECRET_KEY = SECRET_KEY or "dev-only-insecure-key-do-not-use-in-production-0123456789"  # noqa: F405

if not DATABASES:  # noqa: F405  (no DATABASE_URL / DB_NAME given)
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}  # noqa: F405

# Welcome, new-story and password-reset emails are printed in the terminal running the server.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# No Redis needed on your own computer. Unless you set REDIS_URL yourself, the cache lives in memory and background tasks
# (emails, counters) run right away instead of waiting for a Celery worker. A Redis that is installed but not answering
# (the "Timeout reading from socket" error) can then no longer break pages such as /api/v1/analytics/collect/.
if not os.environ.get("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "story-dev"}}  # noqa: F405
    CELERY_TASK_ALWAYS_EAGER = True  # noqa: F405
    CELERY_TASK_EAGER_PROPAGATES = False  # noqa: F405