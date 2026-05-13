import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from reversion.models import Revision, Version

from contracts.models import OperationLog


def version_snapshot(version) -> dict:
    revision = version.revision
    return {
        "version_id": version.pk,
        "revision_id": revision.pk,
        "date_created": timezone.localtime(revision.date_created).isoformat(),
        "user": getattr(revision.user, "username", "") if revision.user else "",
        "comment": revision.comment,
        "object_repr": version.object_repr,
        "format": version.format,
        "serialized_data": version.serialized_data,
    }


class Command(BaseCommand):
    help = "Archive old operation logs and reversion snapshots."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default="", help="Archive output directory.")
        parser.add_argument("--log-retention-days", type=int, default=730)
        parser.add_argument("--snapshot-retention-days", type=int, default=365)
        parser.add_argument("--delete-online-records", action="store_true")

    def handle(self, *args, **options):
        now = timezone.localtime()
        output_dir = Path(options["output_dir"] or settings.BASE_DIR / "archives" / "audit")
        output_dir.mkdir(parents=True, exist_ok=True)

        log_cutoff = timezone.now() - timedelta(days=options["log_retention_days"])
        snapshot_cutoff = timezone.now() - timedelta(days=options["snapshot_retention_days"])
        old_logs = OperationLog.objects.filter(created_at__lt=log_cutoff)
        old_versions = Version.objects.filter(revision__date_created__lt=snapshot_cutoff).select_related("revision", "revision__user")

        payload = {
            "exported_at": now.isoformat(),
            "policy": {
                "operation_logs_online_retention_days": options["log_retention_days"],
                "snapshots_online_retention_days": options["snapshot_retention_days"],
                "delete_online_records": options["delete_online_records"],
            },
            "operation_logs": [
                {
                    "created_at": timezone.localtime(log.created_at).isoformat(),
                    "username": log.username,
                    "role": log.role,
                    "action": log.action,
                    "object_type": log.object_type,
                    "object_name": log.object_name,
                    "object_id": log.object_id,
                    "detail": log.detail,
                    "ip_address": str(log.ip_address or ""),
                }
                for log in old_logs
            ],
            "snapshots": [version_snapshot(version) for version in old_versions],
        }

        archive_path = output_dir / f"audit_archive_{now:%Y%m%d%H%M%S}.json"
        archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        if options["delete_online_records"]:
            old_logs.delete()
            old_versions.delete()
            Revision.objects.filter(version__isnull=True).delete()

        self.stdout.write(self.style.SUCCESS(f"Archived audit data to {archive_path}"))
