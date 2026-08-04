"""
Copy files that still exist on local disk into the configured bucket
(M5 Phase 3, F1).

Run ONCE, after the bucket is configured and before the deploy that starts
writing there — while the current instance's disk still holds whatever
survived the last restart.

Two properties matter more than speed here:

* **Idempotent.** Safe to re-run after a partial failure. A key already in
  the bucket is skipped, never overwritten, so a second pass cannot destroy
  a file the first pass uploaded.
* **Non-destructive.** Nothing is deleted from local disk and no database
  row is modified. The `file` column already holds the storage key, which
  is identical in both backends — that is what makes this a copy rather
  than a migration, and what makes rollback a config change.

Files whose bytes are already gone are reported, not fatal. Four materials
uploaded before M4 Phase C lost their files to a deploy long ago; nothing
can recover those, and the command should not pretend otherwise.
"""

from django.core.files.storage import FileSystemStorage, default_storage
from django.core.management.base import BaseCommand, CommandError

from common.storage import object_storage_enabled
from groups.models import Document, StudyMaterial

# (model, field) pairs holding user-uploaded bytes.
TARGETS = ((StudyMaterial, "file"), (Document, "file"))


class Command(BaseCommand):
    help = "Copy surviving local media files into the configured object storage bucket."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Report what would be copied without writing anything.",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Re-upload keys that already exist in the bucket.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        if not object_storage_enabled():
            raise CommandError(
                "No bucket configured. Set AWS_STORAGE_BUCKET_NAME (plus "
                "credentials and endpoint) before running this."
            )

        # Read from the local disk explicitly rather than through
        # default_storage: by the time this runs, default_storage IS the
        # bucket, so reading through it would copy the bucket onto itself.
        local = FileSystemStorage()

        copied = skipped = missing = failed = 0

        for model, field_name in TARGETS:
            label = model.__name__
            rows = model.objects.exclude(**{field_name: ""}).exclude(
                **{f"{field_name}__isnull": True}
            )
            self.stdout.write(f"\n-> {label}: {rows.count()} row(s) with a file")

            for row in rows.iterator():
                key = getattr(row, field_name).name
                if not key:
                    continue

                if not local.exists(key):
                    missing += 1
                    self.stdout.write(
                        self.style.WARNING(f"   gone     {label}#{row.pk} {key}")
                    )
                    continue

                if not force and default_storage.exists(key):
                    skipped += 1
                    continue

                if dry_run:
                    copied += 1
                    self.stdout.write(f"   would copy {label}#{row.pk} {key}")
                    continue

                try:
                    with local.open(key, "rb") as fh:
                        # save() may return a different name if the backend
                        # de-duplicates; assert it did not, because a
                        # renamed object no longer matches the database row.
                        stored = default_storage.save(key, fh)
                    if stored != key:
                        failed += 1
                        self.stderr.write(self.style.ERROR(
                            f"   RENAMED  {label}#{row.pk} {key} -> {stored}; "
                            f"row now points at a key that does not exist"
                        ))
                        continue
                    copied += 1
                    self.stdout.write(self.style.SUCCESS(f"   copied   {label}#{row.pk} {key}"))
                except Exception as exc:
                    failed += 1
                    self.stderr.write(self.style.ERROR(
                        f"   FAILED   {label}#{row.pk} {key}: {type(exc).__name__}: {exc}"
                    ))

        self.stdout.write("\n" + "=" * 58)
        self.stdout.write(
            f"  copied {copied} · skipped {skipped} (already present) · "
            f"gone {missing} · failed {failed}"
        )
        if dry_run:
            self.stdout.write(self.style.WARNING("  DRY RUN — nothing was written"))
        if missing:
            self.stdout.write(self.style.WARNING(
                f"  {missing} file(s) were already lost to an earlier deploy "
                f"and cannot be recovered."
            ))
        if failed:
            raise CommandError(f"{failed} file(s) failed to copy; re-run to retry.")
        self.stdout.write("=" * 58)
