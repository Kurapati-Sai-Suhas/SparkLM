"""
Audits every question that ships BOTH a custom execution wrapper and a
starter template for the same language, reporting pairs whose contracts
cannot possibly agree.

Read-only: this command never writes. It exists because the mismatch it
detects lives in production data, not in code, so CI cannot see it — a
scheduled or pre-release run against prod is the only way to catch it.

    python manage.py audit_wrapper_templates
    python manage.py audit_wrapper_templates --language java
"""

from django.core.management.base import BaseCommand

from groups.models import Question
from groups.wrapper_contract import check_pair

LANGUAGES = ("python", "java", "cpp", "javascript", "c", "js")


class Command(BaseCommand):
    help = "Report questions whose starter template cannot satisfy their custom wrapper."

    def add_arguments(self, parser):
        parser.add_argument("--language", type=str, default=None,
                            help="Only audit this language.")

    def handle(self, *args, **options):
        only = options["language"]
        languages = (only,) if only else LANGUAGES

        rows = []
        questions = (
            Question.objects
            .exclude(hidden_wrapper_code={})
            .exclude(hidden_wrapper_code__isnull=True)
            .only("id", "title", "hidden_wrapper_code", "boilerplate_code")
        )

        for q in questions.iterator():
            wrappers = q.hidden_wrapper_code or {}
            templates = q.boilerplate_code or {}
            if not isinstance(wrappers, dict) or not isinstance(templates, dict):
                continue
            for lang in languages:
                wrapper = wrappers.get(lang)
                if not (isinstance(wrapper, str) and wrapper.strip()):
                    continue
                template = templates.get(lang)
                has_template = isinstance(template, str) and template.strip()
                problems = check_pair(wrapper, template, lang) if has_template else []
                rows.append({
                    "id": q.id,
                    "title": q.title.strip()[:38],
                    "language": lang,
                    "has_template": bool(has_template),
                    "problems": problems,
                })

        if not rows:
            self.stdout.write(self.style.SUCCESS("No question ships a custom wrapper. Nothing to audit."))
            return

        header = f"{'ID':>6}  {'LANGUAGE':<11} {'WRAPPER':<8} {'TEMPLATE':<9} {'COMPATIBLE':<11} {'RISK':<8} TITLE"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        mismatches = 0
        for row in sorted(rows, key=lambda r: (r["id"], r["language"])):
            if not row["has_template"]:
                # No template means the editor shows a stub and the user writes
                # everything: honest, and impossible to mismatch.
                compatible, risk = "n/a", "none"
            elif row["problems"]:
                compatible, risk = "NO", "HIGH"
                mismatches += 1
            else:
                compatible, risk = "yes", "none"
            self.stdout.write(
                f"{row['id']:>6}  {row['language']:<11} {'yes':<8} "
                f"{('yes' if row['has_template'] else 'no'):<9} {compatible:<11} {risk:<8} {row['title']}"
            )
            for problem in row["problems"]:
                self.stdout.write(self.style.ERROR(f"         └─ {problem}"))

        self.stdout.write("")
        if mismatches:
            self.stdout.write(self.style.ERROR(
                f"{mismatches} incompatible wrapper/template pair(s) — users of those "
                "languages receive a template that cannot compile."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"All {len(rows)} wrapper/template pair(s) are contract-compatible."
            ))
