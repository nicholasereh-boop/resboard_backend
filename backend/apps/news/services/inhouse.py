"""The in-house source: news that our own staff write, with no outside publisher behind it.

The rights gate (services/rights.py) needs every article to belong to an approved source with an approved rights record. For work we
write ourselves that is just paperwork, so this module creates both once, the first time they are needed. After that they are ordinary
rows: staff can see them under "Sources and rights", and suspending the source or revoking the record takes the articles down like any other.
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from ..models import NewsRightsRecord, NewsSource

INHOUSE_SLUG = "resourceboard-editorial"
INHOUSE_NAME = "ResourceBoard"


@transaction.atomic
def get_inhouse_source():
    """The source for articles written by our own staff. Created (approved and active) on first use; never re-enabled if staff switched it off."""
    source, _ = NewsSource.objects.get_or_create(
        slug=INHOUSE_SLUG,
        defaults={
            "name": INHOUSE_NAME, "source_type": NewsSource.SourceType.ORIGINAL, "adapter": NewsSource.Adapter.NONE,
            "approval_state": NewsSource.Approval.APPROVED, "is_active": True, "allows_full_text": True, "allows_commercial_use": True,
            "attribution_template": "{source}", "evidence_ref": "Written in-house by ResourceBoard staff.",
            "notes": "Created automatically for news the team writes itself (no outside source).",
        },
    )
    if not source.rights_records.exists():
        NewsRightsRecord.objects.create(
            source=source, title="In-house writing", licence="ResourceBoard original", basis=NewsRightsRecord.Basis.ORIGINAL,
            allows_full_text=True, allows_commercial_use=True, allows_modification=True, requires_attribution=False,
            evidence_note="Written in-house by ResourceBoard staff.", status=NewsRightsRecord.Status.APPROVED,
            effective_from=timezone.now() - timedelta(minutes=1),
        )
    return source
