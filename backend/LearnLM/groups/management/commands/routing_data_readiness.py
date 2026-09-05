"""
Routing-evaluation data readiness report (M2 P2.30).

READ-ONLY, unconditionally. There is no --apply, no --fix, no flag that
writes. The command has no import that reaches a write path and a structural
test asserts it, because "this command is read-only" is a claim about code
and claims about code are checkable.

    python manage.py routing_data_readiness
    python manage.py routing_data_readiness --json
    python manage.py routing_data_readiness --contract

Its output is the deliverable. A NOT_READY verdict is a successful result:
the report exists to establish whether Traffic Cop can be evaluated honestly,
not to conclude that it can.

The threshold is `retrain_ai.MIN_TRAINING_LABELS`, read rather than restated.
What this report adds is applying it to the labels that can be SHOWN to rest
on verified grading truth, instead of to every row carrying a non-null
outcome — reaching the threshold with untrustworthy labels would satisfy the
training command and still be the wrong thing to train on.
"""

import json

from django.core.management.base import BaseCommand

from groups import routing_readiness


class Command(BaseCommand):
    help = ("Report whether there is enough trustworthy routing data to "
            "evaluate or retrain the Traffic Cop. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true",
                            help="Machine-readable output.")
        parser.add_argument("--contract", action="store_true",
                            help="Print the label contract in full.")

    def handle(self, *args, **options):
        census = routing_readiness.collect_census()
        gate = routing_readiness.evaluate_gate(census)

        if options["json"]:
            self.stdout.write(json.dumps({
                "census": census.as_dict(),
                "gate": gate.as_dict(),
                "label_contract": [
                    {"name": n, "predicate": p, "reason": r}
                    for n, p, r in routing_readiness.LABEL_CONTRACT],
            }, indent=2))
            return

        self._render(census, gate, options["contract"])

    # ── rendering ─────────────────────────────────────────────────────

    def _render(self, census, gate, show_contract):
        write = self.stdout.write

        write(self.style.MIGRATE_HEADING(
            "ROUTING DATA READINESS (M2 P2.30) — read-only"))
        write("")

        if show_contract:
            write("The label contract")
            write("  A decision→outcome pair is usable only if it survives ALL of:")
            for index, (name, predicate, reason) in enumerate(
                    routing_readiness.LABEL_CONTRACT, start=1):
                write(f"    {index}. {name}: {predicate}")
                for line in _wrap(reason):
                    write(f"       {line}")
            write("")

        write("Content")
        write(f"  questions total                    {census.questions_total}")
        write(f"  ORACLE_VERIFIED                    {census.oracle_verified_questions}")
        write(f"  technically servable               {census.servable_questions}")
        write(f"  trusted share of servable pool     "
              f"{census.trusted_share_of_servable * 100:.3f}%")
        write("")

        write("Interactions")
        write(f"  submissions (ANY trust)            {census.submissions_total}")
        write(f"  ADAPTIVE-ELIGIBLE submissions      {census.adaptive_eligible_submissions}")
        write(f"  learners with any submission       {census.learners_with_submissions}")
        write(f"  learners with adaptive interaction {census.learners_with_adaptive_interactions}")
        write(f"  max adaptive per learner           {census.max_submissions_per_learner}")
        write(f"  median adaptive per learner        {census.median_submissions_per_learner}")
        write("")

        write("Routing decisions")
        write(f"  decisions logged                   {census.decisions_total}")
        write(f"  outcome recorded (closed)          {census.decisions_closed}")
        write(f"  still open                         {census.decisions_open}")
        write(f"  TRUSTWORTHY decision→outcome pairs {census.decisions_trustworthy}")
        write(f"  contaminated (cannot be vouched)   {census.decisions_contaminated}")
        write("")
        write("  'closed' is what retrain_ai would train on today. Only the")
        write("  trustworthy count is defensible training data.")
        write("")

        write("Routes")
        write(f"  hierarchical                       {census.hierarchical}")
        write(f"  flat                               {census.flat}")
        write(f"  cold-start decisions               {census.cold_start_decisions}")
        for route, count in census.route_counts.items():
            if route not in ("hierarchical", "flat"):
                write(f"  {route:<34} {count}")
        write("")

        write("Policy attribution")
        for version, count in census.policy_versions.items():
            write(f"  {version:<34} {count}")
        write(f"  without a policy version           "
              f"{census.decisions_without_policy_version}")
        write("")

        write("Outcome balance (trustworthy pairs only)")
        write(f"  correct                            {census.label_positive}")
        write(f"  incorrect                          {census.label_negative}")
        write(f"  minority rate                      "
              f"{census.minority_outcome_rate:.3f}")
        write("")

        write("Integrity")
        write(f"  decisions missing problem_id       "
              f"{census.decisions_missing_problem_id}")
        write(f"  problem_id naming no question      "
              f"{census.decisions_with_unresolvable_problem}")
        write(f"  temporal span (days)               {census.span_days}")
        write("")

        style = (self.style.SUCCESS if gate.verdict != routing_readiness.NOT_READY
                 else self.style.WARNING)
        write(style(f"VERDICT: {gate.verdict}"))
        write(f"  threshold in force: {gate.threshold} trustworthy pairs "
              f"(retrain_ai.MIN_TRAINING_LABELS)")
        for reason in gate.reasons:
            for index, line in enumerate(_wrap(reason)):
                write(f"  - {line}" if index == 0 else f"    {line}")
        for satisfied in gate.satisfied:
            write(f"  + {satisfied}")
        write("")
        write("Nothing was written. This command has no write path.")


def _wrap(text, width=68):
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines
