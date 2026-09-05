"""
Trusted-content coverage and the operator worklist (M2 P2.32).

READ-ONLY, unconditionally. No --apply, no --fix, no flag that writes. A
structural test asserts the source contains no write verb.

    python manage.py trust_coverage
    python manage.py trust_coverage --all          # include covered topics
    python manage.py trust_coverage --json
    python manage.py trust_coverage --pipeline     # who may perform each step

P2.31 made the recommender prefer trusted questions within a difficulty band.
Supply, not exposure, is now the constraint: the policy changed the first pick
in exactly the five topics that hold a verified question and left the rest
alone.

This command says which topic is next and what single artifact blocks it. It
cannot advance anything — every uncovered topic is stopped at
`reference_create`, which needs an authored answer key, and inventing one
would be inventing grading truth.
"""

import json

from django.core.management.base import BaseCommand

from groups import trust_coverage


class Command(BaseCommand):
    help = ("Report trusted-content coverage per topic and the operator "
            "worklist to expand it. Read-only.")

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true",
                            help="Include topics that already have trusted "
                                 "content.")
        parser.add_argument("--json", action="store_true",
                            help="Machine-readable output.")
        parser.add_argument("--pipeline", action="store_true",
                            help="Print the trust pipeline and who may "
                                 "perform each step.")
        parser.add_argument("--candidates", type=int, default=3,
                            metavar="N",
                            help="Shortlist size per topic (default 3).")

    def handle(self, *args, **options):
        coverage = trust_coverage.collect(include_covered=options["all"],
                                          limit=options["candidates"])
        summary = trust_coverage.summarise(coverage)

        if options["json"]:
            self.stdout.write(json.dumps({
                "summary": summary,
                "topics": [c.as_dict() for c in coverage],
                "pipeline": [
                    {"step": s, "authority": a, "note": n}
                    for s, a, n in trust_coverage.TRUST_PIPELINE],
            }, indent=2))
            return

        self._render(coverage, summary, options["pipeline"])

    def _render(self, coverage, summary, show_pipeline):
        write = self.stdout.write

        write(self.style.MIGRATE_HEADING(
            "TRUSTED CONTENT COVERAGE (M2 P2.32) — read-only"))
        write("")

        if show_pipeline:
            write("The trust pipeline, and who may perform each step")
            for step, authority, note in trust_coverage.TRUST_PIPELINE:
                write(f"  {step:<20} {authority}")
                for line in _wrap(note):
                    write(f"      {line}")
            write("")

        write(f"  topics reported                    "
              f"{summary['topics_reported']}")
        write(f"  uncovered                          {summary['uncovered']}")
        write(f"  blocked on reference authoring     "
              f"{summary['blocked_on_reference_authoring']}")
        write(f"  blocked on content repair first    "
              f"{summary['blocked_on_content_repair']}")
        write(f"  median learner Elo (exposure axis) "
              f"{summary['median_learner_elo']}")
        write("")

        ordered = sorted(coverage, key=lambda c: (c.depth, -c.servable,
                                                  c.name))
        for entry in ordered:
            marker = "OK " if entry.trusted else "-> "
            unlocks = (f"  unlocks {', '.join(entry.unlocks)}"
                       if entry.unlocks else "")
            write(self.style.MIGRATE_LABEL(
                f"{marker}{entry.name}  (depth {entry.depth}){unlocks}"))
            write(f"     questions {entry.questions}   servable "
                  f"{entry.servable}   trusted {entry.trusted}")
            write(f"     reference {entry.reference_solutions}   oracle "
                  f"{entry.oracle_executions}   approved {entry.approvals}")
            if entry.blocker:
                for line in _wrap(f"BLOCKED ON: {entry.blocker}"):
                    write(f"     {line}")
            for candidate in entry.candidates:
                reach = "reachable" if candidate["reachable"] else "OUT OF BAND"
                mark = "  " if candidate["executable"] else "! "
                write(f"     {mark}q{candidate['id']:<6} "
                      f"{candidate['difficulty']:>6.0f}  {reach:<11} "
                      f"{candidate['title']}")
                if candidate["harness_blocker"]:
                    for line in _wrap(
                            f"NOT EXECUTABLE: {candidate['harness_blocker']}"):
                        write(f"           {line}")
            write("")

        write("Candidates are a MECHANICAL shortlist — whether the signature")
        write("is executable, whether the difficulty is reachable under the")
        write("P2.31 exposure policy, how many peers share it, then id.")
        write("")
        write("A candidate WITHOUT a '!' is not verified to work. It means no")
        write("blocker is provable from its signature. An unannotated")
        write("parameter, or a tree declared as `list`, still receives a raw")
        write("string — the signature cannot show that, so check the stdin")
        write("format before authoring against it.")
        write("")
        write("Nothing here knows whether a question is well-posed or worth")
        write("your time. The operator picks.")
        write("")
        write("This command advances nothing and wrote nothing. Authoring a")
        write("reference solution is authoring grading truth, and no report")
        write("may do that.")


def _wrap(text, width=66):
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
