"""
export_languages — generate the frontend's copy of the language registry
(M4 Phase B polish).

Phase B unified the BACKEND's three language maps into `common/languages.py`.
It left the frontend holding its own hand-maintained copies, and a review of
the result found **four** definitions in total:

  1. common/languages.py                     (source of truth)
  2. src/lib/editorTemplates.ts              CANONICAL_LANGUAGES, BOILERPLATE_KEYS,
                                             SELF_CONTAINED_LANGUAGES
  3. src/components/LanguageSelector.tsx     LANGUAGES (value, label, ext)
  4. src/lib/editorTemplates.test.ts         a hardcoded expected list

The deciding fact: the backend registry ALREADY carries `label` and
`extension` — precisely what LanguageSelector duplicates. The source of truth
held everything the frontend needed and simply was not delivering it.

Codegen for five languages would normally cost more than it saves. It does
not here, because the drift is user-facing: a language present in the picker
but absent from the backend means a student can pick it, write a solution and
be told the language is unsupported. That is the bug Phase B already fixed
once (`c` missing from ALLOWED_LANGUAGES).

Usage:
    python manage.py export_languages           # write the file
    python manage.py export_languages --check   # fail if stale (CI)
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from common.languages import export_payload

# .../commands/[0] management/[1] common/[2] LearnLM/[3] backend/[4] repo-root/[5]
REPO_ROOT = Path(__file__).resolve().parents[5]
OUTPUT = REPO_ROOT / "studysphere-ai-11" / "src" / "lib" / "languages.generated.json"


def rendered():
    """Exact bytes the file should contain. Stable ordering, trailing newline."""
    return json.dumps(export_payload(), indent=2, sort_keys=False) + "\n"


class Command(BaseCommand):
    help = "Generate the frontend language registry from common/languages.py."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit non-zero if the generated file is missing or stale. For CI.",
        )

    def handle(self, *args, **options):
        payload = rendered()

        if options["check"]:
            if not OUTPUT.exists():
                raise CommandError(
                    f"{OUTPUT.name} is missing. Run `manage.py export_languages`."
                )
            if OUTPUT.read_text(encoding="utf-8") != payload:
                raise CommandError(
                    f"{OUTPUT.name} is stale — common/languages.py has changed. "
                    f"Run `manage.py export_languages` and commit the result."
                )
            self.stdout.write(self.style.SUCCESS(f"{OUTPUT.name} is up to date."))
            return

        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(payload, encoding="utf-8")
        count = len(export_payload()["languages"])
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {count} languages to {OUTPUT.relative_to(REPO_ROOT)}."
        ))
