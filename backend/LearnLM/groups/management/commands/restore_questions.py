"""
Restores questions for specific topics from the original LeetCode CSV.

Why this exists: the pre-rewrite seed_dsa_dag deleted the "DSA Masterclass"
portal on every run. Topic.portal and Question.topic both cascade, so the
run that preceded the seeder rewrite silently deleted every question under
the six topics that had been absorbed into the portal (Two Pointers, Stack,
Binary Search, Greedy, Backtracking, Bit Manipulation) — about 109 rows.

This command re-imports ONLY the missing rows for the requested topics from
data/Leetcode_Questions_updated (2024-11-02).csv, using the same mapping as
bulk_seed. Restored questions get placeholder content containing the
reseed_questions marker, so the normal reseed pipeline can regenerate their
full statements and test cases.

Existing titles are never duplicated. Dry-run by default; --apply to write.

Usage:
    python manage.py restore_questions
    python manage.py restore_questions --apply
    python manage.py restore_questions --topics "Stack,Greedy" --apply
"""

import ast
import csv
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from groups.models import Topic, Question

CSV_NAME = 'Leetcode_Questions_updated (2024-11-02).csv'

# The six topics wiped by the old seeder's portal-delete cascade.
DEFAULT_TOPICS = [
    "Two Pointers", "Stack", "Binary Search",
    "Greedy", "Backtracking", "Bit Manipulation",
]

# Shared marker so restored rows are picked up by the reseed pipeline and
# excluded from recommendations until seeded.
PLACEHOLDER_MARKER = Question.PLACEHOLDER_MARKER


class Command(BaseCommand):
    help = 'Restores missing questions for given topics from the LeetCode CSV. Dry-run unless --apply.'

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true",
                            help="Actually write to the database.")
        parser.add_argument("--topics", type=str, default=",".join(DEFAULT_TOPICS),
                            help="Comma-separated topic names to restore.")

    @staticmethod
    def _primary_tag(raw):
        """
        Topic_tags holds a Python-list repr string, e.g. "['Two Pointers', 'String']".
        Returns the first tag, or '' for empty/missing tags.
        """
        raw = (raw or '').strip()
        if not raw:
            return ''
        if raw.startswith('['):
            try:
                tags = ast.literal_eval(raw)
                return tags[0].strip() if tags else ''
            except (ValueError, SyntaxError):
                return raw.strip("[]'\" ").split(',')[0].strip()
        return raw.split(',')[0].strip()

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        target_topics = {t.strip() for t in options["topics"].split(",") if t.strip()}

        csv_path = os.path.join(settings.BASE_DIR, 'data', CSV_NAME)
        if not os.path.exists(csv_path):
            self.stderr.write(self.style.ERROR(f"CSV not found: {csv_path}"))
            return

        existing_titles = {
            t.strip() for t in Question.objects.values_list('title', flat=True)
        }

        to_create = []
        per_topic = {}

        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            for row in csv.DictReader(csvfile):
                topic_name = self._primary_tag(row.get('Topic_tags'))
                if topic_name not in target_topics:
                    continue

                title = (row.get('Question') or 'Unknown Title').strip()
                if not title or title in existing_titles:
                    continue

                difficulty_str = (row.get('Difficulty') or 'Medium').lower()
                if difficulty_str == 'easy':
                    base_elo = 1000.0
                elif difficulty_str == 'hard':
                    base_elo = 1600.0
                else:
                    base_elo = 1300.0

                q_link = row.get('Question_Link', '#')
                content = (
                    f"{PLACEHOLDER_MARKER} {title} problem.\n\n"
                    f"<p>Original problem: <a href='{q_link}' target='_blank'>{q_link}</a></p>"
                )

                to_create.append(Question(
                    title=title,
                    topic=None,  # placeholder, set below once topics are resolved
                    content=content,
                    base_difficulty=base_elo,
                    boilerplate_code={
                        "python": "class Solution:\n    def solve(self, *args, **kwargs):\n        pass",
                        "java": "class Solution {\n    public void solve() {\n    }\n}",
                    },
                    hidden_test_cases=[],
                ))
                to_create[-1]._topic_name = topic_name
                existing_titles.add(title)
                per_topic[topic_name] = per_topic.get(topic_name, 0) + 1

        if not to_create:
            self.stdout.write(self.style.SUCCESS("Nothing missing — all CSV rows for these topics already exist."))
            return

        for name, n in sorted(per_topic.items()):
            self.stdout.write(f"  {name}: {n} question(s) to restore")

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                f"\nDRY RUN — would restore {len(to_create)} question(s). Re-run with --apply."
            ))
            return

        with transaction.atomic():
            topic_cache = {}
            for q in to_create:
                name = q._topic_name
                if name not in topic_cache:
                    topic_cache[name], _ = Topic.objects.get_or_create(
                        name=name, defaults={"structure_type": "hierarchical"}
                    )
                q.topic = topic_cache[name]
            Question.objects.bulk_create(to_create)

        self.stdout.write(self.style.SUCCESS(
            f"✅ Restored {len(to_create)} question(s) as reseedable placeholders. "
            "Run reseed_questions to regenerate their content and test cases."
        ))
