"""
One end-to-end agent scenario, deterministically (M2 P2.14 §24I).

    python manage.py agent_demo --user <username>
    python manage.py agent_demo --user <username> --live

Default is a SCRIPTED planner: no provider, no key, no network, no quota, and
the same answer every time. That is what makes it a demo rather than a
gamble — a recruiter-facing walkthrough must not depend on whether a free
tier reset overnight.

`--live` swaps in the real provider. Everything else is identical, so the
difference between the two runs is exactly "did an LLM choose the tools".

── What it writes ──────────────────────────────────────────────────────────

Nothing. It reads learner state, reads the Glicko signal, reads the KT
signal, reads prerequisites, reads candidates, and prints a recommendation.
`grade_submission` is never called, so no submission, rating, mastery or
trust state moves. Verified by a counts check before and after.

── Why it does not need the Oracle ─────────────────────────────────────────

§24I: the demo must not be blocked on publication. It uses whatever the bank
already trusts. If nothing is trusted it says so plainly and still shows the
loop running — an empty candidate set is a real state the agent handles, not
a failure to hide.
"""

import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from groups.agent import kt_signal
from groups.agent import tools as toolkit
from groups.agent.orchestrator import Orchestrator

User = get_user_model()

LEARNER_REQUEST = "What should I practise next?"


def scripted_planner(question_id_holder):
    """
    The fixed plan a competent model would produce for this request.

    Written out rather than generated so the demo is reproducible: observe
    state, look at what is servable, read the one it picked, then explain.
    """
    steps = [
        {"tool": "get_learner_state", "arguments": {},
         "reasoning": "start from what the backend already knows"},
        {"tool": "get_candidate_problems", "arguments": {"limit": 5},
         "reasoning": "only the backend may decide what is servable"},
    ]
    produced = {"n": 0}

    def plan(observation):
        index = produced["n"]
        produced["n"] += 1

        if index < len(steps):
            return steps[index]

        # Read the first candidate the BACKEND offered — never an id of the
        # planner's own invention.
        if index == len(steps) and question_id_holder.get("id"):
            return {"tool": "get_problem_context",
                    "arguments": {"question_id": question_id_holder["id"]},
                    "reasoning": "read the one I intend to recommend"}

        return {"final": question_id_holder.get("answer")
                or "There is no verified problem to recommend yet.",
                "reasoning": "explain the choice"}

    return plan


class Command(BaseCommand):
    help = ("Run one end-to-end agent recommendation. Reads only; writes "
            "nothing.")

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True)
        parser.add_argument("--live", action="store_true",
                            help="Use the configured LLM provider instead of "
                                 "the scripted planner.")
        parser.add_argument("--json", action="store_true")

    def handle(self, *args, **options):
        from groups.models import CodeSubmission, Question

        user = User.objects.filter(username=options["user"]).first()
        if user is None:
            raise CommandError(f"no such user: {options['user']}")

        before = (CodeSubmission.objects.count(),
                  Question.objects.filter(
                      trust_state=Question.TRUST_ORACLE_VERIFIED).count())

        session = toolkit.Session(user=user)
        holder = {}

        # Pre-read what the backend is willing to serve, so the scripted
        # planner can name a REAL id. This is the same call the agent makes;
        # doing it here keeps the script honest rather than clairvoyant.
        offered = toolkit.get_candidate_problems(session, limit=5)
        candidate = (offered.get("candidates") or [None])[0]
        if candidate:
            holder["id"] = candidate["question_id"]
            holder["answer"] = (
                f"Practise \"{candidate['title']}\" next. It is the closest "
                f"trusted problem to your current level (difficulty "
                f"{candidate['difficulty']}).")

        if options["live"]:
            from groups.agent import provider
            planner = provider.llm_planner()
            mode = "live provider"
        else:
            planner = scripted_planner(holder)
            mode = "scripted (deterministic)"

        result = Orchestrator(session, planner).run(LEARNER_REQUEST)

        state = toolkit.get_learner_state(session)
        kt_reading = kt_signal.predict(user)

        after = (CodeSubmission.objects.count(),
                 Question.objects.filter(
                     trust_state=Question.TRUST_ORACLE_VERIFIED).count())

        if options["json"]:
            self.stdout.write(json.dumps({
                "mode": mode, "request": LEARNER_REQUEST,
                "result": result.as_dict(),
                "glicko_readings": state["glicko_readings"],
                "kt_prediction": kt_reading,
                "wrote_nothing": before == after,
            }, indent=2, default=str))
            return

        self._render(mode, result, state, kt_reading, offered, before, after)

    # ── output ────────────────────────────────────────────────────────

    def _render(self, mode, result, state, kt_reading, offered, before, after):
        write, style = self.stdout.write, self.style

        write(style.MIGRATE_HEADING(f"AGENT DEMO — {mode}"))
        write(f'  learner asks: "{LEARNER_REQUEST}"')
        write("")

        write(style.MIGRATE_HEADING("1. Learner state (Elo — the live engine)"))
        write(f"  elo rating            {state['elo_rating']}")
        write(f"  rating engine         {state['rating_engine']}")
        write(f"  distinct solved       {state['distinct_questions_solved']}")
        write(f"  admissible evidence   "
              f"{state['submissions_admissible_as_evidence']}")
        write("")

        write(style.MIGRATE_HEADING("2. Glicko-2 (READ-ONLY signal)"))
        if state["glicko_readings"]:
            for reading in state["glicko_readings"]:
                write(f"  {reading['topic']:<22} rating {reading['rating']:>7} "
                      f"rd {reading['rating_deviation']:>6}  "
                      f"confidence {reading['confidence']:.3f}  "
                      f"(n={reading['evidence_count']})")
        else:
            write("  no per-topic Glicko evidence for this learner yet")
        write(style.WARNING(
            "  Not the routing authority. Elo decides what is served."))
        write("")

        write(style.MIGRATE_HEADING("3. TA-GTKT (READ-ONLY signal)"))
        write(f"  status                {kt_reading['status']}")
        write(f"  predicted mastery     {kt_reading['predicted_mastery']}")
        write(f"  predicted next correct {kt_reading['predicted_next_correct']}")
        if kt_reading.get("model"):
            write(f"  model                 {kt_reading['model']} "
                  f"{kt_reading.get('model_version') or ''}")
            write(f"  trained on            {kt_reading.get('trained_on')}")
        else:
            write(f"  reason                {kt_reading['reason']}")
        for line in _wrap(kt_reading["applicability"], 66):
            write(style.WARNING(f"  {line}"))
        write("")

        write(style.MIGRATE_HEADING("4. Candidates (server-controlled)"))
        write(f"  trust filter          {offered['trust_filter']}")
        write(f"  offered               {offered['count']}")
        for row in offered["candidates"][:5]:
            write(f"    #{row['question_id']:<7} {row['title'][:44]:<46} "
                  f"{row['difficulty']}")
        write("")

        write(style.MIGRATE_HEADING("5. Agent loop"))
        for step, phrase in enumerate(result.transcript, start=1):
            write(f"  {step}. {phrase}")
        for call in result.calls:
            write(f"     - {call['tool']:<24} {call['outcome']:<8} "
                  f"reads_only={call.get('reads_only')}")
        write("")

        write(style.MIGRATE_HEADING("6. Recommendation"))
        for line in _wrap(result.answer, 68):
            write(f"  {line}")
        write(f"  (stopped_because: {result.stopped_because})")
        write("")

        if before == after:
            write(style.SUCCESS(
                "  WROTE NOTHING — submissions and trust states unchanged"))
        else:
            write(style.ERROR(
                f"  STATE CHANGED: {before} -> {after}. This demo must be "
                f"read-only; investigate before using it."))


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(str(text), width) or [""]
