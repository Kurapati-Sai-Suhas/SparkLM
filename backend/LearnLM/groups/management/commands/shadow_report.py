"""
Compare the shadow adaptive model against production routing (M2 P2.9a).

READ-ONLY with respect to production. It reads `LearnerTopicSkill` /
`QuestionSkill` and the same candidate queryset production uses, and reports
where the two would disagree. It changes no learner state, no grading data and
no routing behaviour.

    python manage.py shadow_report
    python manage.py shadow_report --json
    python manage.py shadow_report --seed 7

Agreement is NOT a quality measure and this command does not present it as
one. A disagreement means the two systems weigh evidence differently, which is
the entire reason the shadow exists; it is not by itself evidence that either
is better. Deciding that needs learner outcomes on verified questions, which
do not exist yet.
"""

import json
from collections import Counter

from django.core.management.base import BaseCommand

from groups import glicko, shadow
from groups.coding_views import _candidate_questions, _select_question
from groups.models import LearnerTopicSkill, Topic


class Command(BaseCommand):
    help = "Compare shadow (Glicko-2) recommendations against production routing."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Emit the report as JSON.")
        parser.add_argument("--seed", type=int, default=0,
                            help="Seed for Thompson sampling (reproducibility).")
        parser.add_argument("--limit", type=int, default=0,
                            help="Only report the first N learner/topic pairs.")

    def handle(self, *args, **options):
        seed = options["seed"]
        rows = []

        pairs = (LearnerTopicSkill.objects
                 .select_related("user", "topic")
                 .order_by("user_id", "topic_id"))
        if options["limit"]:
            pairs = pairs[:options["limit"]]

        for skill in pairs:
            user, topic = skill.user, skill.topic
            candidates = list(_candidate_questions(user, topic_name=topic.name))

            production = _select_question(user, topic.name, _target_elo(user))
            shadow_q, observation = shadow.select_question(
                user, topic, candidates, seed=seed)

            observation.production_question_id = (
                production.pk if production else None)
            observation.agree = (
                (production.pk if production else None)
                == (shadow_q.pk if shadow_q else None))
            rows.append(observation.as_dict())

        report = self._summarise(rows, seed)
        if options["json"]:
            self.stdout.write(json.dumps(
                {"summary": report, "observations": rows}, indent=2))
        else:
            self._print(report, rows)

    # ── reporting ────────────────────────────────────────────

    def _summarise(self, rows, seed):
        if not rows:
            return {"pairs": 0, "seed": seed,
                    "note": "no shadow learner state exists yet"}

        agreed = sum(1 for r in rows if r["agree"])
        distances = [r["difficulty_distance"] for r in rows
                     if r["difficulty_distance"] is not None]
        rds = [r["learner_rd"] for r in rows]
        successes = [r["predicted_success"] for r in rows
                     if r["predicted_success"] is not None]

        return {
            "pairs": len(rows),
            "seed": seed,
            "agreement_rate": round(agreed / len(rows), 4),
            "agreed": agreed,
            "disagreed": len(rows) - agreed,
            "mean_difficulty_distance": (
                round(sum(distances) / len(distances), 2) if distances else None),
            "mean_learner_rd": round(sum(rds) / len(rds), 2),
            "high_uncertainty_pairs": sum(1 for r in rds if r >= 200),
            "low_uncertainty_pairs": sum(1 for r in rds if r < 100),
            "mean_predicted_success": (
                round(sum(successes) / len(successes), 4)
                if successes else None),
            "by_topic": dict(Counter(r["topic"] for r in rows)),
            "target_success_probability": shadow.TARGET_SUCCESS_PROBABILITY,
        }

    def _print(self, report, rows):
        if not rows:
            self.stdout.write(self.style.WARNING(
                "No shadow learner state exists yet. The shadow model only "
                "records adaptive_eligible evidence, and nothing in the bank "
                "is ORACLE_VERIFIED, so this is the expected state."))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nShadow vs production routing (synthetic/local data only)"))
        for key, value in report.items():
            self.stdout.write(f"  {key:32s} {value}")

        self.stdout.write("")
        self.stdout.write(
            f"  {'topic':<18}{'rating':>9}{'RD':>8}{'sampled':>10}"
            f"{'prod':>7}{'shadow':>8}{'agree':>7}{'P(win)':>9}")
        self.stdout.write("  " + "-" * 78)
        for r in rows[:40]:
            self.stdout.write(
                f"  {r['topic'][:17]:<18}{r['learner_rating']:>9.0f}"
                f"{r['learner_rd']:>8.0f}{r['sampled_ability']:>10.0f}"
                f"{str(r['production_question_id'] or '-'):>7}"
                f"{str(r['shadow_question_id'] or '-'):>8}"
                f"{'yes' if r['agree'] else 'no':>7}"
                f"{(r['predicted_success'] if r['predicted_success'] is not None else 0):>9.2f}")

        self.stdout.write("")
        self.stdout.write(
            "  Agreement is not a quality measure. A disagreement means the "
            "two systems weigh evidence\n  differently — which is why the "
            "shadow exists — not that either is better. That needs learner\n"
            "  outcomes on ORACLE_VERIFIED questions, which do not exist yet.")


def _target_elo(user):
    """The production ability estimate, read exactly as NextProblemView does."""
    from groups.models import UserCodingProfile
    profile = UserCodingProfile.objects.filter(user=user).first()
    return profile.elo_rating if profile else glicko.DEFAULT_RATING
