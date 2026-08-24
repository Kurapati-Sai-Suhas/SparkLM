"""
Knowledge-tracing data readiness report (M2 P2.10a).

READ-ONLY, unconditionally. There is no --apply, no --fix, no flag that writes.
The command has no import that reaches a write path and a structural test
asserts it, because "this command is read-only" is a claim about code and
claims about code are checkable.

    python manage.py kt_data_readiness
    python manage.py kt_data_readiness --json
    python manage.py kt_data_readiness --features

Its output is the deliverable of P2.10a. If the verdict is NOT_READY, that is a
successful result: the phase exists to establish whether a Transformer can be
trained honestly, not to conclude that it can.
"""

import json

from django.core.management.base import BaseCommand

from groups import kt_features, kt_leakage, kt_readiness


class Command(BaseCommand):
    help = ("Report how many interactions are eligible for knowledge-tracing "
            "training, and whether the data supports it. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Machine-readable output.")
        parser.add_argument("--features", action="store_true",
                            help="Include the full feature inventory.")
        parser.add_argument(
            "--thresholds", metavar="PATH",
            help="JSON overriding the proposed research thresholds. They are "
                 "candidates requiring empirical validation, not constants.")

    def handle(self, *args, **options):
        thresholds = self._load_thresholds(options.get("thresholds"))
        census = kt_readiness.collect_census()

        # Causality is audited over the real projection. With zero eligible
        # rows the audit is vacuously safe, which is reported as such rather
        # than presented as a clean bill of health.
        interactions = _project(census)
        leakage = kt_leakage.audit_causality(interactions)

        gate = kt_readiness.evaluate_gate(census, thresholds, leakage)

        if options["json"]:
            payload = {
                "census": census.as_dict(),
                "gate": gate.as_dict(),
                "leakage": leakage.as_dict(),
                "filter_contract": [
                    {"name": n, "predicate": p, "reason": r}
                    for n, p, r in kt_readiness.FILTER_CONTRACT],
            }
            if options["features"]:
                payload["features"] = kt_features.as_dict()
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        self._render(census, gate, leakage, options["features"])

    # ── inputs ────────────────────────────────────────────────────────

    def _load_thresholds(self, path):
        if not path:
            return kt_readiness.ReadinessThresholds()
        import pathlib
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        return kt_readiness.ReadinessThresholds(**data)

    # ── output ────────────────────────────────────────────────────────

    def _render(self, census, gate, leakage, show_features):
        write, style = self.stdout.write, self.style

        write(style.MIGRATE_HEADING(
            "KT DATA READINESS (M2 P2.10a) — read-only"))
        write("")

        write(style.MIGRATE_HEADING("The filtering contract"))
        write("  An interaction is KT-eligible only if it survives ALL of:")
        for index, (name, predicate, reason) in enumerate(
                kt_readiness.FILTER_CONTRACT, start=1):
            write(f"    {index}. {name}: {predicate}")
            for line in _wrap(reason, 68):
                write(f"       {line}")
        write("")

        write(style.MIGRATE_HEADING("Volume"))
        write(f"  total submissions (ANY trust)      {census.total_interactions}")
        write(f"  KT-ELIGIBLE interactions           "
              f"{style.SUCCESS(str(census.eligible_interactions))}")
        write(f"  excluded                           {census.ineligible_interactions}")
        write(f"  eligible share                     {census.eligible_percentage}%")
        write("")
        write(style.WARNING(
            "  'total submissions' is NOT a dataset size. Only the eligible "
            "count\n  is trustworthy training data."))
        write("")

        write(style.MIGRATE_HEADING("Trust"))
        write(f"  questions total                    {census.total_questions}")
        write(f"  ORACLE_VERIFIED                    "
              f"{census.oracle_verified_questions}")
        write(f"  without trustworthy grading truth  "
              f"{census.questions_without_trustworthy_evidence}")
        write("")

        if census.eligible_interactions:
            write(style.MIGRATE_HEADING("Breadth and depth"))
            write(f"  learners                           {census.eligible_learners}")
            write(f"  questions                          {census.eligible_questions}")
            write(f"  topics                             {census.eligible_topics}")
            write(f"  learners >= 20 interactions        {census.learners_ge_20}")
            write(f"  learners >= 50 interactions        {census.learners_ge_50}")
            write(f"  learners >= 100 interactions       {census.learners_ge_100}")
            write(f"  cold-start learners (< 5)          {census.cold_start_learners}")
            write(f"  median depth                       {census.median_depth}")
            write(f"  depth histogram                    {census.depth_histogram}")
            write("")

            write(style.MIGRATE_HEADING("Distributions"))
            write(f"  outcomes    {census.outcome_distribution}")
            write(f"  minority outcome rate  {census.minority_outcome_rate:.4f}")
            write(f"  languages   {census.language_distribution}")
            write(f"  per topic   {_truncate(census.per_topic_counts)}")
            write("")

            write(style.MIGRATE_HEADING("Temporal coverage"))
            write(f"  earliest    {census.earliest}")
            write(f"  latest      {census.latest}")
            write(f"  span        {census.span_days} days")
            write("")

        write(style.MIGRATE_HEADING("Leakage safety"))
        for check in leakage.checks_run:
            write(f"  checked: {check}")
        if leakage.is_safe and not census.eligible_interactions:
            write(style.WARNING(
                "  VACUOUSLY SAFE — zero interactions to audit. This is not "
                "evidence\n  that the projection is causal."))
        elif leakage.is_safe:
            write(style.SUCCESS("  no causality violations found"))
        else:
            for problem in leakage.problems:
                write(style.ERROR(f"  {problem}"))
        write("")
        write("  point-in-time Glicko:")
        for line in _wrap(kt_leakage.GLICKO_RECONSTRUCTION, 66):
            write(f"    {line}")
        write("")

        verdict_style = (style.SUCCESS
                         if gate.verdict == kt_readiness.TRAINING_READY
                         else style.WARNING
                         if gate.verdict == kt_readiness.RESEARCH_READY
                         else style.ERROR)
        write(style.MIGRATE_HEADING("GATE"))
        write(f"  verdict: {verdict_style(gate.verdict)}")
        write("")
        if gate.reasons:
            write("  blocking:")
            for reason in gate.reasons:
                write(style.ERROR(f"    - {reason}"))
        if gate.satisfied:
            write("  satisfied:")
            for line in gate.satisfied:
                write(style.SUCCESS(f"    - {line}"))
        write("")
        write(style.WARNING(
            "  Thresholds are PROPOSED research candidates, not validated "
            "constants.\n  They are order-of-magnitude arguments from the KT "
            "literature's own\n  experimental settings and have not been "
            "tested on LearnLM data."))

        if show_features:
            write("")
            write(style.MIGRATE_HEADING("Feature inventory"))
            for status, items in sorted(kt_features.by_status().items()):
                write(f"  {status} ({len(items)})")
                for feature in items:
                    write(f"    {feature.name:38} {feature.verdict}")
            write("")
            write(style.ERROR("  MUST-HAVE instrumentation gaps:"))
            for feature in kt_features.must_have_gaps():
                write(style.ERROR(f"    - {feature.name}"))


def _project(census):
    """
    Build the in-memory interaction projection for the causality audit.

    Derived, never stored. P2.10a creates no table: every field is a column on
    CodeSubmission or a pure causal function of rows that already exist, so the
    projection can be rebuilt at will and cannot disagree with its source.
    """
    if not census.eligible_interactions:
        return []

    rows = (kt_readiness.eligible_interactions()
            .values("user_id", "question_id", "question__topic_id",
                    "submitted_at", "status", "language")
            .order_by("submitted_at", "id"))

    interactions, prior_attempts, previous_at = [], {}, {}
    for row in rows:
        learner = row["user_id"]
        key = (learner, row["question_id"])
        last = previous_at.get(learner)
        interactions.append(kt_leakage.Interaction(
            learner_id=learner,
            question_id=row["question_id"],
            topic_id=row["question__topic_id"],
            submitted_at=row["submitted_at"],
            outcome=1 if row["status"] == "accepted" else 0,
            attempt_number=prior_attempts.get(key, 0),
            lag_seconds=(0.0 if last is None
                         else (row["submitted_at"] - last).total_seconds()),
            language=row["language"] or "",
        ))
        prior_attempts[key] = prior_attempts.get(key, 0) + 1
        previous_at[learner] = row["submitted_at"]

    return interactions


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [""]


def _truncate(mapping, limit=8):
    items = list(mapping.items())[:limit]
    suffix = f" ... (+{len(mapping) - limit})" if len(mapping) > limit else ""
    return dict(items).__repr__() + suffix
