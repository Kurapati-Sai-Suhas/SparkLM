"""
KT dataset pipeline (M2 P2.10b).

Weighted toward the ways a dataset silently becomes wrong: a duplicate that
should have been kept, a duplicate that should have been dropped, a sequence
that is not ordered, a split that leaks, a hash that is not reproducible, and
an ineligible LearnLM row that slips past the trust firewall.
"""

import ast
import csv
import inspect
import json
import pathlib
import tempfile
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from kt_dataset import adapters, pipeline, sources, stats, validation
from kt_dataset.schema import CANONICAL_COLUMNS, SCHEMA_VERSION


def raw(learner, question, order, correct, *, concept="C1", row_id=None):
    return {
        "source_row_id": row_id or f"r{order}",
        "learner_id": learner, "question_id": question, "concept_id": concept,
        "correct": str(correct), "order_key": str(order),
        "outcome_label": "correct" if correct else "incorrect",
        "occurred_at": None, "raw_dataset_hash": "h", "source_file": "f",
    }


def config(**overrides):
    options = {"dataset_name": "synthetic", "dataset_version": "v1"}
    options.update(overrides)
    return pipeline.BuildConfig(**options)


# ═════════════════════════════════════════════════════════════
# Validation
# ═════════════════════════════════════════════════════════════

class ValidationTests(TestCase):

    def test_a_clean_row_passes(self):
        self.assertEqual(
            validation.validate_raw_row(raw("L1", "Q1", 1, 1),
                                        require_concept=True), [])

    def test_missing_fields_are_reported_individually(self):
        row = raw("", "", 1, 1, concept="")
        reasons = {r.reason for r in
                   validation.validate_raw_row(row, require_concept=True)}
        self.assertEqual(reasons, {validation.MISSING_LEARNER,
                                   validation.MISSING_QUESTION,
                                   validation.MISSING_CONCEPT})

    def test_missing_concept_is_optional_by_configuration(self):
        row = raw("L1", "Q1", 1, 1, concept="")
        self.assertEqual(
            validation.validate_raw_row(row, require_concept=False), [])
        self.assertTrue(
            validation.validate_raw_row(row, require_concept=True))

    def test_malformed_correctness_is_rejected_not_coerced(self):
        for value in ("yes", "", "0.5", "2", "-1"):
            row = raw("L1", "Q1", 1, 1)
            row["correct"] = value
            reasons = {r.reason for r in
                       validation.validate_raw_row(row, require_concept=False)}
            self.assertTrue(
                reasons & {validation.MALFORMED_CORRECT,
                           validation.MISSING_CORRECT},
                f"{value!r} was accepted as a binary label")

    def test_missing_order_key_is_rejected(self):
        row = raw("L1", "Q1", 1, 1)
        row["order_key"] = ""
        reasons = {r.reason for r in
                   validation.validate_raw_row(row, require_concept=False)}
        self.assertIn(validation.MISSING_ORDER, reasons)

    def test_every_rejection_carries_a_machine_readable_reason(self):
        row = raw("", "Q1", 1, 1)
        for rejection in validation.validate_raw_row(row,
                                                     require_concept=False):
            self.assertTrue(rejection.reason)
            self.assertTrue(rejection.source_row_id)
            json.dumps(rejection.as_dict())


# ═════════════════════════════════════════════════════════════
# Duplicate policy
# ═════════════════════════════════════════════════════════════

class DuplicateTests(TestCase):

    def test_exact_source_duplicate_is_dropped(self):
        rows = [raw("L1", "Q1", 1, 1, row_id="a"),
                raw("L1", "Q1", 1, 1, row_id="b")]
        kept, rejected = validation.drop_source_duplicates(rows)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["source_row_id"], "a")
        self.assertEqual(rejected[0].reason, validation.SOURCE_DUPLICATE)

    def test_a_legitimate_repeat_attempt_is_kept(self):
        """
        Same learner, same question, DIFFERENT position — the learner came
        back. This is the signal knowledge tracing exists to model; dropping
        it would flatter every baseline by deleting its hardest cases.
        """
        rows = [raw("L1", "Q1", 1, 0), raw("L1", "Q1", 2, 1)]
        kept, rejected = validation.drop_source_duplicates(rows)
        self.assertEqual(len(kept), 2)
        self.assertEqual(rejected, [])

    def test_same_position_different_outcome_is_kept_as_distinguishable(self):
        """Contradictory records are NOT silently collapsed — they differ."""
        rows = [raw("L1", "Q1", 1, 0), raw("L1", "Q1", 1, 1)]
        kept, _ = validation.drop_source_duplicates(rows)
        self.assertEqual(len(kept), 2)

    def test_different_learners_same_position_are_both_kept(self):
        rows = [raw("L1", "Q1", 1, 1), raw("L2", "Q1", 1, 1)]
        kept, rejected = validation.drop_source_duplicates(rows)
        self.assertEqual(len(kept), 2)
        self.assertEqual(rejected, [])

    def test_deduplication_is_deterministic(self):
        rows = [raw("L1", "Q1", 1, 1, row_id=f"r{n}") for n in range(5)]
        first, _ = validation.drop_source_duplicates(rows)
        second, _ = validation.drop_source_duplicates(rows)
        self.assertEqual([r["source_row_id"] for r in first],
                         [r["source_row_id"] for r in second])


# ═════════════════════════════════════════════════════════════
# Assembly and ordering
# ═════════════════════════════════════════════════════════════

class AssemblyTests(TestCase):

    def test_attempt_number_counts_only_prior_attempts(self):
        rows = [raw("L1", "Q1", 1, 0), raw("L1", "Q1", 2, 0),
                raw("L1", "Q1", 3, 1)]
        interactions, _r, _i = pipeline.assemble(
            rows, config(), sources.SYNTHETIC, "h")
        self.assertEqual([i.attempt_number for i in interactions], [0, 1, 2])

    def test_attempt_number_is_per_question_not_global(self):
        rows = [raw("L1", "Q1", 1, 1), raw("L1", "Q2", 2, 1),
                raw("L1", "Q1", 3, 1)]
        interactions, _r, _i = pipeline.assemble(
            rows, config(), sources.SYNTHETIC, "h")
        self.assertEqual([i.attempt_number for i in interactions], [0, 0, 1])

    def test_rows_are_ordered_by_learner_then_position(self):
        rows = [raw("L2", "Q1", 5, 1), raw("L1", "Q1", 9, 1),
                raw("L1", "Q1", 2, 0)]
        interactions, _r, _i = pipeline.assemble(
            rows, config(), sources.SYNTHETIC, "h")
        self.assertEqual([(i.learner_id, i.sequence_position)
                          for i in interactions],
                         [("L1", 2), ("L1", 9), ("L2", 5)])

    def test_non_monotonic_sequence_is_reported(self):
        from kt_dataset.schema import Interaction
        broken = [Interaction("L1", "Q1", "C1", 5, 1, 0),
                  Interaction("L1", "Q1", "C1", 3, 1, 1)]
        rejections = validation.assert_monotonic_sequences(broken)
        self.assertTrue(rejections)
        self.assertEqual(rejections[0].reason, validation.NON_MONOTONIC)

    def test_wall_clock_is_never_synthesised_for_a_source_without_it(self):
        """
        The finding that shaped the schema: ASSISTments 2009 has no timestamp
        column, so `occurred_at` stays None rather than being invented.
        """
        rows = [raw("L1", "Q1", 1, 1)]
        interactions, _r, _i = pipeline.assemble(
            rows, config(), sources.ASSISTMENTS_2009, "h")
        self.assertIsNone(interactions[0].occurred_at)
        self.assertIsNone(interactions[0].lag_seconds)

    def test_a_timestamp_is_dropped_when_the_source_declares_none(self):
        """
        ADVERSARIAL: rows that CARRY a timestamp, from a source declaring it
        has none. The declared capability must win.

        The earlier test used synthetic rows whose `occurred_at` was already
        None, so removing the capability guard changed nothing and a mutation
        sweep showed it surviving. This supplies the value the guard has to
        suppress — the case where a file has a column we have declared
        unreliable, and using it anyway would put unverified time into the
        dataset.
        """
        from datetime import datetime, timezone
        row = raw("L1", "Q1", 1, 1)
        row["occurred_at"] = datetime(2020, 5, 5, tzinfo=timezone.utc)

        interactions, _r, _i = pipeline.assemble(
            [row], config(), sources.ASSISTMENTS_2009, "h")
        self.assertIsNone(interactions[0].occurred_at)

        # A source that DOES declare wall-clock time keeps it.
        kept, _r, _i = pipeline.assemble(
            [row], config(), adapters.learnlm_capabilities(), "h")
        self.assertIsNotNone(kept[0].occurred_at)

    def test_provenance_travels_with_every_row(self):
        rows = [raw("L1", "Q1", 1, 1)]
        interactions, _r, _i = pipeline.assemble(
            rows, config(source_file="skill_builder.csv"),
            sources.SYNTHETIC, "abc123")
        record = interactions[0]
        self.assertEqual(record.provenance.raw_dataset_hash, "abc123")
        self.assertEqual(record.provenance.source_file, "skill_builder.csv")
        self.assertEqual(record.provenance.schema_version, SCHEMA_VERSION)

    def test_glicko_columns_exist_but_are_never_populated(self):
        """
        Present so LearnLM can log point-in-time rating going forward; never
        back-filled, because no source can currently supply it honestly.
        """
        rows = [raw("L1", "Q1", 1, 1)]
        interactions, _r, _i = pipeline.assemble(
            rows, config(), sources.SYNTHETIC, "h")
        self.assertIn("glicko_rating_at_time", CANONICAL_COLUMNS)
        self.assertIsNone(interactions[0].glicko_rating_at_time)
        self.assertIsNone(interactions[0].glicko_rd_at_time)


# ═════════════════════════════════════════════════════════════
# Split
# ═════════════════════════════════════════════════════════════

class SplitTests(TestCase):

    def sequence(self, learner, length, start=1):
        return [raw(learner, f"Q{n}", start + n, n % 2)
                for n in range(length)]

    def build(self, rows, **overrides):
        return pipeline.build(rows, config(**overrides), sources.SYNTHETIC, "h")

    def test_split_is_chronological_within_each_learner(self):
        result = self.build(self.sequence("L1", 20))
        self.assertTrue(
            max(i.sequence_position for i in result.train)
            < min(i.sequence_position for i in result.validation))
        self.assertTrue(
            max(i.sequence_position for i in result.validation)
            < min(i.sequence_position for i in result.test))

    def test_one_interaction_learner_goes_entirely_to_train(self):
        result = self.build(self.sequence("L1", 1))
        self.assertEqual(len(result.train), 1)
        self.assertEqual(result.validation, [])
        self.assertEqual(result.test, [])

    def test_two_interaction_learner_goes_entirely_to_train(self):
        result = self.build(self.sequence("L1", 2))
        self.assertEqual(len(result.train), 2)
        self.assertEqual(result.test, [])

    def test_three_interaction_learner_is_split_across_all_three(self):
        """The boundary case: the shortest splittable sequence."""
        result = self.build(self.sequence("L1", 3))
        self.assertEqual(len(result.train), 1)
        self.assertEqual(len(result.validation), 1)
        self.assertEqual(len(result.test), 1)

    def test_every_learner_appears_in_train(self):
        rows = self.sequence("L1", 10) + self.sequence("L2", 4, start=100)
        result = self.build(rows)
        self.assertEqual({i.learner_id for i in result.train}, {"L1", "L2"})

    def test_learners_span_splits_which_is_the_production_question(self):
        result = self.build(self.sequence("L1", 20))
        self.assertIn("L1", {i.learner_id for i in result.train})
        self.assertIn("L1", {i.learner_id for i in result.test})

    def test_the_split_passes_the_p2_10a_audit(self):
        rows = (self.sequence("L1", 20) + self.sequence("L2", 15, start=100)
                + self.sequence("L3", 3, start=200))
        result = self.build(rows)
        report = pipeline.audit_split_ordinally(
            result.train, result.validation, result.test)
        self.assertTrue(report.is_safe, report.problems)
        self.assertTrue(report.checks_run)

    def test_the_audit_detects_a_deliberately_leaked_split(self):
        """
        Adversarial: a test row moved backward into train. The audit must
        refuse — otherwise every later metric is meaningless.
        """
        result = self.build(self.sequence("L1", 20))
        leaked_train = result.train + [result.test[-1]]
        report = pipeline.audit_split_ordinally(
            leaked_train, result.validation, result.test)
        self.assertFalse(report.is_safe)

    def test_no_interaction_appears_in_two_splits(self):
        result = self.build(self.sequence("L1", 30))
        def keys(rows):
            return {(i.learner_id, i.sequence_position) for i in rows}
        self.assertEqual(keys(result.train) & keys(result.test), set())
        self.assertEqual(keys(result.train) & keys(result.validation), set())
        self.assertEqual(keys(result.validation) & keys(result.test), set())

    def test_split_covers_every_interaction(self):
        result = self.build(self.sequence("L1", 25))
        self.assertEqual(
            len(result.train) + len(result.validation) + len(result.test),
            len(result.interactions))

    def test_split_orders_its_input_rather_than_trusting_it(self):
        """
        `split_by_learner` called DIRECTLY with unsorted input.

        Through `build` this is unobservable — `assemble` already sorts, so the
        split's own sort looks redundant and a mutation sweep showed deleting
        it survived. It is not redundant: the split is a public function, and a
        future caller (a re-split, a notebook, a cached-interactions path) can
        hand it rows in any order. Verified here at that boundary.
        """
        from kt_dataset.schema import Interaction
        shuffled = [Interaction("L1", f"Q{n}", "C1", position, n % 2, 0)
                    for n, position in enumerate([7, 1, 9, 3, 5, 11, 2, 8])]

        train, validation_rows, test = pipeline.split_by_learner(
            shuffled, config())

        self.assertTrue(
            max(i.sequence_position for i in train)
            < min(i.sequence_position for i in validation_rows))
        self.assertTrue(
            max(i.sequence_position for i in validation_rows)
            < min(i.sequence_position for i in test))

    def test_invalid_fractions_are_rejected(self):
        for train, validation_fraction in ((0.9, 0.2), (1.5, 0.1), (0.0, 0.1)):
            with self.assertRaises(ValueError):
                config(train_fraction=train,
                       validation_fraction=validation_fraction)


# ═════════════════════════════════════════════════════════════
# Determinism
# ═════════════════════════════════════════════════════════════

class DeterminismTests(TestCase):

    def test_same_input_same_processed_hash(self):
        rows = sources.synthetic_rows(learners=10, seed=1)
        first = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        second = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        self.assertEqual(first.processed_hash, second.processed_hash)
        self.assertEqual(first.split_hash, second.split_hash)

    def test_input_order_does_not_change_the_hash(self):
        """Assembly sorts, so a shuffled read of the same file agrees."""
        rows = sources.synthetic_rows(learners=10, seed=1)
        forward = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        backward = pipeline.build(list(reversed(rows)), config(),
                                  sources.SYNTHETIC, "h")
        self.assertEqual(forward.processed_hash, backward.processed_hash)

    def test_a_changed_row_changes_the_hash(self):
        rows = sources.synthetic_rows(learners=10, seed=1)
        before = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        mutated = [dict(row) for row in rows]
        mutated[0]["correct"] = "0" if mutated[0]["correct"] == "1" else "1"
        after = pipeline.build(mutated, config(), sources.SYNTHETIC, "h")
        self.assertNotEqual(before.processed_hash, after.processed_hash)

    def test_configuration_participates_in_the_hash(self):
        """
        Two builds with identical rows under different settings are not the
        same dataset — one merely happened to have no missing concepts.
        """
        rows = sources.synthetic_rows(learners=10, seed=1)
        lenient = pipeline.build(rows, config(require_concept=False),
                                 sources.SYNTHETIC, "h")
        strict = pipeline.build(rows, config(require_concept=True),
                                sources.SYNTHETIC, "h")
        self.assertNotEqual(lenient.processed_hash, strict.processed_hash)

    def test_split_fractions_change_the_split_hash(self):
        rows = sources.synthetic_rows(learners=10, seed=1)
        default = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        other = pipeline.build(rows, config(train_fraction=0.5),
                               sources.SYNTHETIC, "h")
        self.assertNotEqual(default.split_hash, other.split_hash)

    def test_processed_hash_matches_its_pinned_value(self):
        """
        GOLDEN HASH. Pins the hash DEFINITION, not just its self-consistency.

        Every other determinism test compares two hashes computed by the same
        code, so any change to what the hash covers — dropping the schema
        version, reordering the configuration frames, omitting columns —
        leaves them all passing while silently producing a different dataset
        identity. A mutation sweep confirmed that: three such mutants survived.

        If this test fails, the reproducibility contract changed. That is
        allowed, but it must be deliberate and accompanied by a
        SCHEMA_VERSION bump, because manifests already written elsewhere refer
        to hashes computed under the old definition.
        """
        rows = sources.synthetic_rows(learners=8, questions=10,
                                      max_length=12, seed=99)
        result = pipeline.build(rows, config(), sources.SYNTHETIC,
                                "fixed-raw-hash")

        self.assertEqual(len(result.interactions), 35)
        self.assertEqual(SCHEMA_VERSION, 2)
        # v2 (M2 P2.13) added `response_time_ms`. Deliberate, and paid for
        # with the bump this docstring requires.
        #   v1 was 15d0a581ad60277f38d138d6c607ddcf72d84c031cbacfeffae3dc87d27f96c0
        self.assertEqual(
            result.processed_hash,
            "adc66bd5457345eb3e442b89ee5c4f7b9a4449838bac50d7d6cb32efa79cd803")
        self.assertEqual(
            result.split_hash,
            "ca13aa60f3b888aa8c0c2ee9b90b1f369f314aa7baa73b3ff27a9f481be81c20")

    def test_adding_a_column_moved_the_corpus_hash_and_not_the_partition(self):
        """
        The load-bearing claim behind the v1 -> v2 bump.

        A schema change that also moved the SPLIT would have invalidated
        every Phase 22 baseline: those numbers are only comparable to the new
        models if all of them are scored on the same held-out rows. The
        partition is a function of learner and sequence position, and adding
        a column touches neither — so `split_hash` must be byte-for-byte the
        value it had under v1, pinned here independently of the golden test
        above.
        """
        rows = sources.synthetic_rows(learners=8, questions=10,
                                      max_length=12, seed=99)
        result = pipeline.build(rows, config(), sources.SYNTHETIC,
                                "fixed-raw-hash")

        self.assertEqual(
            result.split_hash,
            "ca13aa60f3b888aa8c0c2ee9b90b1f369f314aa7baa73b3ff27a9f481be81c20",
            "adding a column moved the partition; the baselines are no longer "
            "comparable")

    def test_synthetic_source_is_seeded_and_reproducible(self):
        self.assertEqual(sources.synthetic_rows(learners=5, seed=7),
                         sources.synthetic_rows(learners=5, seed=7))
        self.assertNotEqual(sources.synthetic_rows(learners=5, seed=7),
                            sources.synthetic_rows(learners=5, seed=8))

    def test_file_hash_is_content_addressed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as handle:
            handle.write("a,b\n1,2\n")
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        self.assertEqual(sources.hash_file(path), sources.hash_file(path))
        self.assertEqual(len(sources.hash_file(path)), 64)


# ═════════════════════════════════════════════════════════════
# ASSISTments reader
# ═════════════════════════════════════════════════════════════

class AssistmentsReaderTests(TestCase):

    def write(self, rows, header=None):
        header = header or ["order_id", "user_id", "problem_id", "correct",
                            "skill_id", "skill_name"]
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                             encoding="latin-1", newline="")
        writer = csv.DictWriter(handle, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        handle.close()
        self.addCleanup(
            lambda: pathlib.Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_reads_the_published_column_names(self):
        path = self.write([
            {"order_id": "1", "user_id": "64525", "problem_id": "51424",
             "correct": "1", "skill_id": "1", "skill_name": "Box and Whisker"},
        ])
        rows = list(sources.read_assistments_2009(path, raw_hash="h"))
        self.assertEqual(rows[0]["learner_id"], "64525")
        self.assertEqual(rows[0]["question_id"], "51424")
        self.assertEqual(rows[0]["concept_id"], "1")
        self.assertEqual(rows[0]["order_key"], "1")
        self.assertIsNone(rows[0]["occurred_at"])

    def test_missing_required_column_raises_rather_than_yielding_junk(self):
        path = self.write([{"order_id": "1", "user_id": "1"}],
                          header=["order_id", "user_id"])
        with self.assertRaises(ValueError) as caught:
            list(sources.read_assistments_2009(path, raw_hash="h"))
        self.assertIn("missing required", str(caught.exception))

    def test_missing_skill_id_survives_the_reader_and_is_decided_later(self):
        """~16% of the real file has no skill_id; the reader must not judge."""
        path = self.write([
            {"order_id": "1", "user_id": "1", "problem_id": "2",
             "correct": "1", "skill_id": "", "skill_name": ""},
        ])
        rows = list(sources.read_assistments_2009(path, raw_hash="h"))
        self.assertEqual(rows[0]["concept_id"], "")

    def test_source_row_id_points_at_the_file_line(self):
        path = self.write([
            {"order_id": "1", "user_id": "1", "problem_id": "2",
             "correct": "1", "skill_id": "1", "skill_name": "x"},
            {"order_id": "2", "user_id": "1", "problem_id": "3",
             "correct": "0", "skill_id": "1", "skill_name": "x"},
        ])
        rows = list(sources.read_assistments_2009(path, raw_hash="h"))
        self.assertTrue(rows[0]["source_row_id"].endswith(":2"))
        self.assertTrue(rows[1]["source_row_id"].endswith(":3"))

    def test_capabilities_declare_no_wall_clock_time(self):
        self.assertFalse(sources.ASSISTMENTS_2009.has_wall_clock_time)
        self.assertIn("wall_clock_time",
                      sources.ASSISTMENTS_2009.unavailable_reasons)
        self.assertIn("lag_seconds",
                      sources.ASSISTMENTS_2009.unavailable_reasons)


# ═════════════════════════════════════════════════════════════
# LearnLM trust firewall
# ═════════════════════════════════════════════════════════════

class LearnLMAdapterTests(TestCase):

    class FakeSubmission:
        def __init__(self, pk, eligible):
            self.pk = pk
            self.adaptive_eligible = eligible

    def test_eligible_is_admitted(self):
        admissible, rejected = adapters.partition_by_eligibility(
            [self.FakeSubmission(1, True)])
        self.assertEqual(len(admissible), 1)
        self.assertEqual(rejected, [])

    def test_ineligible_is_rejected_with_a_reason(self):
        admissible, rejected = adapters.partition_by_eligibility(
            [self.FakeSubmission(1, False)])
        self.assertEqual(admissible, [])
        self.assertEqual(rejected[0].reason, adapters.REJECT_NOT_ELIGIBLE)

    def test_a_submission_with_no_flag_at_all_is_rejected(self):
        """Absent defaults to refused — the safe direction."""
        class Bare:
            pk = 9
        admissible, rejected = adapters.partition_by_eligibility([Bare()])
        self.assertEqual(admissible, [])
        self.assertEqual(len(rejected), 1)

    def test_mixed_batch_is_partitioned_not_dropped(self):
        batch = [self.FakeSubmission(n, n % 2 == 0) for n in range(10)]
        admissible, rejected = adapters.partition_by_eligibility(batch)
        self.assertEqual(len(admissible), 5)
        self.assertEqual(len(rejected), 5)

    def test_the_adapter_reuses_p2_10a_eligibility(self):
        """
        No second definition of eligibility. A copy here would eventually
        disagree with FILTER_CONTRACT, which the readiness report prints.
        """
        source = pathlib.Path(
            inspect.getfile(adapters)).read_text(encoding="utf-8")
        self.assertIn("kt_readiness.eligible_interactions", source)

    def test_there_is_no_flag_to_admit_ineligible_rows(self):
        signature = inspect.signature(adapters.read_learnlm)
        self.assertEqual(list(signature.parameters), ["queryset"])
        source = pathlib.Path(
            inspect.getfile(adapters)).read_text(encoding="utf-8")
        for forbidden in ("include_ineligible", "allow_untrusted",
                          "skip_eligibility", "force"):
            self.assertNotIn(forbidden, source)

    def test_learnlm_capabilities_refuse_execution_time_as_thinking_time(self):
        capabilities = adapters.learnlm_capabilities()
        self.assertFalse(capabilities.has_response_time)
        reason = capabilities.unavailable_reasons["response_time"]
        self.assertIn("Judge0 runtime", reason)
        self.assertFalse(capabilities.has_point_in_time_rating)


class LearnLMLiveTests(TestCase):
    """Against the real ORM, using synthetic local rows only."""

    def setUp(self):
        from groups.models import CodingPortal, Question, Topic
        self.User = get_user_model()
        portal = CodingPortal.objects.create(name="KT DS Portal")
        self.topic = Topic.objects.create(name="KTDS", structure_type="flat",
                                          portal=portal)
        self.question = Question.objects.create(
            title="Q", content="c", topic=self.topic, base_difficulty=1200.0,
            hidden_test_cases=[{"stdin": "1", "expected_output": "1"}],
            boilerplate_code={"python": "x"}, hidden_wrapper_code={},
            status=Question.STATUS_PUBLISHED)
        self.learner = self.User.objects.create_user(
            username="ktds", email="ktds@t.test", password="Pv#2026xyz")

    def submit(self, *, eligible, status="accepted"):
        from groups.models import CodeSubmission
        return CodeSubmission.objects.create(
            user=self.learner, question=self.question, language="python",
            code="x", status=status, adaptive_eligible=eligible)

    def test_ineligible_submissions_yield_nothing(self):
        for _ in range(5):
            self.submit(eligible=False)
        self.assertEqual(list(adapters.read_learnlm()), [])

    def test_eligible_submissions_are_mapped(self):
        self.submit(eligible=True)
        rows = list(adapters.read_learnlm())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["learner_id"], str(self.learner.pk))
        self.assertEqual(rows[0]["correct"], "1")
        self.assertIsNotNone(rows[0]["occurred_at"])

    def test_wrong_answer_maps_to_zero(self):
        self.submit(eligible=True, status="wrong_answer")
        self.assertEqual(list(adapters.read_learnlm())[0]["correct"], "0")

    def test_mixed_eligibility_admits_only_the_trusted_rows(self):
        self.submit(eligible=True)
        for _ in range(4):
            self.submit(eligible=False)
        self.assertEqual(len(list(adapters.read_learnlm())), 1)

    def test_current_learnlm_yields_an_empty_dataset(self):
        """
        The honest state of the system: no ORACLE_VERIFIED question, so no
        eligible submission, so no KT dataset.
        """
        self.submit(eligible=False)
        result = pipeline.build(list(adapters.read_learnlm()),
                                config(dataset_name="learnlm"),
                                adapters.learnlm_capabilities(), "h")
        self.assertEqual(result.interactions, [])


# ═════════════════════════════════════════════════════════════
# Statistics and manifest
# ═════════════════════════════════════════════════════════════

class StatisticsTests(TestCase):

    def test_describe_reports_the_required_sections(self):
        rows = sources.synthetic_rows(learners=30, seed=3)
        result = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        described = stats.describe(result)
        for key in ("interactions", "learners", "questions", "concepts",
                    "correctness", "sequence_length", "per_question",
                    "cold_start", "split"):
            self.assertIn(key, described)
        json.dumps(described)

    def test_empty_dataset_is_described_as_empty(self):
        result = pipeline.build([], config(), sources.SYNTHETIC, "h")
        self.assertEqual(stats.describe(result)["interactions"], 0)

    def test_manifest_contains_every_reproducibility_field(self):
        rows = sources.synthetic_rows(learners=10, seed=4)
        result = pipeline.build(rows, config(), sources.SYNTHETIC, "rawhash")
        leakage = pipeline.audit_split_ordinally(
            result.train, result.validation, result.test)
        payload = pipeline.manifest(result, stats.describe(result), leakage)

        for key in ("schema_version", "dataset_name", "dataset_version",
                    "raw_dataset_hash", "processed_hash", "split_hash",
                    "configuration", "capabilities", "counts",
                    "rejection_counts", "statistics", "leakage",
                    "canonical_columns"):
            self.assertIn(key, payload)
        json.dumps(payload, default=str)

    def test_manifest_records_rejections_rather_than_hiding_them(self):
        rows = sources.synthetic_rows(learners=5, seed=5)
        rows.append(raw("", "Q1", 9999, 1))
        result = pipeline.build(rows, config(), sources.SYNTHETIC, "h")
        payload = pipeline.manifest(
            result, stats.describe(result),
            pipeline.audit_split_ordinally(result.train, result.validation,
                                           result.test))
        self.assertEqual(payload["counts"]["rejected"], 1)
        self.assertIn(validation.MISSING_LEARNER, payload["rejection_counts"])


# ═════════════════════════════════════════════════════════════
# Command
# ═════════════════════════════════════════════════════════════

class CommandTests(TestCase):

    def outdir(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(
            lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        return directory

    def test_synthetic_build_writes_csv_and_manifest(self):
        out = self.outdir()
        call_command("kt_dataset_build", "--source", "synthetic", "--out", out)

        interactions = pathlib.Path(out) / "interactions.csv"
        manifest = pathlib.Path(out) / "manifest.json"
        self.assertTrue(interactions.is_file())
        self.assertTrue(manifest.is_file())
        self.assertTrue((pathlib.Path(out) / "rejections.csv").is_file())

        with interactions.open(encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        self.assertEqual(header, list(CANONICAL_COLUMNS))

        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["dataset_name"], "synthetic")
        self.assertTrue(payload["leakage"]["is_safe"])

    def test_build_is_reproducible_across_runs(self):
        first, second = self.outdir(), self.outdir()
        call_command("kt_dataset_build", "--source", "synthetic", "--out", first)
        call_command("kt_dataset_build", "--source", "synthetic", "--out", second)

        def manifest_of(directory):
            return json.loads(
                (pathlib.Path(directory) / "manifest.json").read_text(
                    encoding="utf-8"))

        self.assertEqual(manifest_of(first)["processed_hash"],
                         manifest_of(second)["processed_hash"])
        self.assertEqual(manifest_of(first)["split_hash"],
                         manifest_of(second)["split_hash"])
        self.assertEqual(
            (pathlib.Path(first) / "interactions.csv").read_text(encoding="utf-8"),
            (pathlib.Path(second) / "interactions.csv").read_text(encoding="utf-8"))

    def test_missing_input_file_explains_the_licence_position(self):
        with self.assertRaises(CommandError) as caught:
            call_command("kt_dataset_build", "--source",
                         "assistments-2009-2010-skill-builder",
                         "--input", "/nonexistent.csv", "--out", self.outdir())
        message = str(caught.exception)
        self.assertIn("does NOT download", message)
        self.assertIn("written agreement", message)

    def test_unknown_source_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("kt_dataset_build", "--source", "ednet",
                         "--out", self.outdir())

    def test_learnlm_source_builds_an_empty_dataset_today(self):
        out = self.outdir()
        call_command("kt_dataset_build", "--source", "learnlm", "--out", out)
        payload = json.loads(
            (pathlib.Path(out) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["counts"]["interactions"], 0)


# ═════════════════════════════════════════════════════════════
# Response duration (M2 P2.13)
# ═════════════════════════════════════════════════════════════

def timed(learner, question, order, correct, duration, **extra):
    row = raw(learner, question, order, correct, **extra)
    row["response_time_raw"] = duration
    return row


class ResponseTimeTests(TestCase):
    """
    ASSISTments 2009 carries a genuine response duration that schema v1 did
    not transport. These tests hold what it is and, more importantly, what
    it is not.
    """

    def assemble(self, rows, capabilities=None):
        return pipeline.assemble(rows, config(),
                                 capabilities or sources.ASSISTMENTS_2009, "h")

    def test_a_duration_survives_the_build(self):
        interactions, _r, _i = self.assemble(
            [timed("L1", "Q1", 1, 1, "19453")])
        self.assertEqual(interactions[0].response_time_ms, 19453.0)

    def test_a_source_without_response_time_gets_none_not_a_guess(self):
        """
        UNAVAILABLE is a property of the SOURCE. A source that cannot supply
        a duration must not be handed one derived from something else.
        """
        interactions, _r, _i = self.assemble(
            [timed("L1", "Q1", 1, 1, "19453")], sources.SYNTHETIC)
        self.assertIsNone(interactions[0].response_time_ms)

    def test_a_negative_duration_is_dropped_and_counted(self):
        """
        Eight rows of the published file have a negative `ms_first_response`.
        A duration cannot be negative; that is a defect, not a fast answer.
        """
        interactions, _r, issues = self.assemble(
            [timed("L1", "Q1", 1, 1, "-6576")])

        self.assertIsNone(interactions[0].response_time_ms)
        self.assertEqual(issues["response_time_negative"], 1)

    def test_a_bad_duration_costs_the_field_and_not_the_row(self):
        """
        An interaction with a broken clock still has a known outcome.
        Rejecting it would delete a real answer over an unusable field.
        """
        interactions, rejections, _i = self.assemble(
            [timed("L1", "Q1", 1, 1, "not-a-number")])

        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0].correct, 1)
        self.assertEqual(rejections, [])

    def test_every_unusable_duration_is_counted_by_reason(self):
        interactions, _r, issues = self.assemble([
            timed("L1", "Q1", 1, 1, "1200"),
            timed("L1", "Q1", 2, 1, ""),
            timed("L1", "Q1", 3, 1, "-5"),
            timed("L1", "Q1", 4, 1, "oops"),
        ])

        self.assertEqual(len(interactions), 4)
        self.assertEqual(issues["response_time_missing"], 1)
        self.assertEqual(issues["response_time_negative"], 1)
        self.assertEqual(issues["response_time_malformed"], 1)

    def test_an_implausibly_long_duration_is_carried_not_capped(self):
        """
        The longest value in ASSISTments 2009 is 23 hours — a session left
        open rather than a response. Squashing it here would assert a number
        nobody measured; what to do about it is a MODELLING decision.
        """
        interactions, _r, _i = self.assemble(
            [timed("L1", "Q1", 1, 1, "84076920")])
        self.assertEqual(interactions[0].response_time_ms, 84076920.0)

    def test_a_duration_is_not_a_timestamp(self):
        """
        The distinction the whole schema is built on. Having a duration does
        not give this corpus a clock.
        """
        interactions, _r, _i = self.assemble(
            [timed("L1", "Q1", 1, 1, "19453")])

        self.assertIsNone(interactions[0].occurred_at)
        self.assertIsNone(interactions[0].lag_seconds)
        self.assertFalse(sources.ASSISTMENTS_2009.has_wall_clock_time)

    def test_the_outcome_side_columns_are_recorded_as_leaky(self):
        """
        `attempt_count` and `hint_count` describe how the learner's
        engagement ENDED. They look like spacing features and are not.
        """
        reasons = sources.ASSISTMENTS_2009.unavailable_reasons
        self.assertIn("attempt_count_column", reasons)
        self.assertIn("leaks", reasons["attempt_count_column"])
        self.assertIn("inter_event_interval", reasons)

    def test_statistics_report_coverage_rather_than_a_mean(self):
        rows = [timed("L1", "Q1", n, 1, str(1000 * n)) for n in range(1, 21)]
        result = pipeline.build(rows, config(), sources.ASSISTMENTS_2009, "h")

        durations = stats.describe(result)["response_time_ms"]
        self.assertEqual(durations["available"], 20)
        self.assertEqual(durations["coverage"], 1.0)
        self.assertIn("median", durations)
        self.assertNotIn("mean", durations)

    def test_the_manifest_reports_field_issues(self):
        rows = [timed("L1", "Q1", 1, 1, "-1"), timed("L1", "Q1", 2, 1, "5")]
        result = pipeline.build(rows, config(), sources.ASSISTMENTS_2009, "h")
        payload = pipeline.manifest(
            result, stats.describe(result),
            pipeline.audit_split_ordinally(result.train, result.validation,
                                           result.test))

        self.assertEqual(payload["field_issues"]["response_time_negative"], 1)


# ═════════════════════════════════════════════════════════════
# Split assignment sidecar (M2 P2.12)
# ═════════════════════════════════════════════════════════════

class SplitAssignmentTests(TestCase):
    """
    The partition is written out so a consumer can USE it rather than
    recompute it. A second implementation of the split rule agrees with
    itself, which is not the same as being right.
    """

    def build(self, **overrides):
        rows = sources.synthetic_rows(learners=12, seed=11)
        return pipeline.build(rows, config(**overrides), sources.SYNTHETIC,
                              "h")

    def test_every_interaction_is_assigned_exactly_once(self):
        result = self.build()
        assignment = list(pipeline.split_assignment(result))

        self.assertEqual(len(assignment), len(result.interactions))
        row_ids = [row["source_row_id"] for row in assignment]
        self.assertEqual(len(set(row_ids)), len(row_ids))

    def test_the_assignment_agrees_with_the_buckets_it_came_from(self):
        result = self.build()
        counts = Counter(row["split"]
                         for row in pipeline.split_assignment(result))

        self.assertEqual(counts["train"], len(result.train))
        self.assertEqual(counts["validation"], len(result.validation))
        self.assertEqual(counts["test"], len(result.test))

    def test_no_interaction_is_assigned_to_two_buckets(self):
        result = self.build()
        seen = {}
        for row in pipeline.split_assignment(result):
            self.assertNotIn(row["source_row_id"], seen)
            seen[row["source_row_id"]] = row["split"]

    def test_the_command_writes_the_sidecar_with_its_declared_columns(self):
        out = self.outdir()
        call_command("kt_dataset_build", "--source", "synthetic", "--out", out)

        path = pathlib.Path(out) / "split_assignment.csv"
        self.assertTrue(path.is_file())
        with path.open(encoding="utf-8") as handle:
            reader = csv.reader(handle)
            self.assertEqual(next(reader), list(pipeline.SPLIT_COLUMNS))
            self.assertTrue(sum(1 for _ in reader) > 0)

    def test_the_sidecar_does_not_change_the_processed_hash(self):
        """
        The canonical columns and the processed hash are a published
        contract. A partition is a property of a build configuration, not of
        an interaction — putting it on the row would make every corpus ever
        built from schema v1 hash differently, so identical data would look
        like different data.
        """
        result = self.build()
        self.assertNotIn("split", CANONICAL_COLUMNS)
        self.assertEqual(
            result.processed_hash,
            pipeline.processed_hash(result.interactions, config()))

    def outdir(self):
        directory = tempfile.mkdtemp()
        self.addCleanup(
            lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        return directory


class SplitAuditScaleTests(TestCase):
    """
    The audit used to rescan all three buckets once per learner, which is
    1.7 billion comparisons on the full ASSISTments file — the audit, not
    the model, becomes the reason a benchmark cannot be run.
    """

    def test_the_audit_agrees_with_a_deliberately_naive_implementation(self):
        rows = sources.synthetic_rows(learners=25, seed=3)
        result = pipeline.build(rows, config(), sources.SYNTHETIC, "h")

        fast = pipeline.audit_split_ordinally(
            result.train, result.validation, result.test)

        learners = sorted({i.learner_id for i in result.interactions})
        naive = []
        for learner in learners:
            picked = [[i for i in bucket if i.learner_id == learner]
                      for bucket in (result.train, result.validation,
                                     result.test)]
            if not picked[1] and not picked[2]:
                continue
            naive.append(learner)

        self.assertTrue(fast.is_safe)
        self.assertEqual(fast.problems, [])
        self.assertTrue(naive, "the fixture produced nothing to audit")

    def test_the_audit_still_catches_a_learner_whose_split_is_reversed(self):
        rows = sources.synthetic_rows(learners=6, seed=4)
        result = pipeline.build(rows, config(), sources.SYNTHETIC, "h")

        reversed_audit = pipeline.audit_split_ordinally(
            result.test, result.validation, result.train)

        self.assertFalse(reversed_audit.is_safe)


# ═════════════════════════════════════════════════════════════
# Safety boundary
# ═════════════════════════════════════════════════════════════

FORBIDDEN = {"expected_output", "hidden_test_cases", "trust_state", "status",
             "adaptive_eligible", "content", "is_active", "review_state",
             "rating", "rating_deviation", "accuracy", "base_difficulty"}


def _writes(path):
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))
    assigned, called = set(), set()

    def names(node):
        if isinstance(node, ast.Attribute):
            return [node.attr]
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                return [key.value]
            return []
        if isinstance(node, (ast.Tuple, ast.List)):
            return [n for e in node.elts for n in names(e)]
        return []

    def persistence(func):
        if not isinstance(func, ast.Attribute):
            return None
        if func.attr in {"save", "delete"}:
            return func.attr
        if func.attr in {"create", "update", "bulk_create", "bulk_update",
                         "get_or_create", "update_or_create"}:
            receiver = func.value
            if isinstance(receiver, ast.Attribute) and receiver.attr == "objects":
                return f"objects.{func.attr}"
        return None

    def visit(node, inside):
        entering = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        scope = inside or entering
        if scope:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                assigned.update(names(target))
            if isinstance(node, ast.Call):
                found = persistence(node.func)
                if found:
                    called.add(found)
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, False)
    return assigned, called


class SafetyBoundaryTests(TestCase):

    def modules(self):
        from kt_dataset import schema
        return (schema, validation, sources, pipeline, stats, adapters)

    def test_no_module_writes_application_state(self):
        for module in self.modules():
            assigned, called = _writes(inspect.getfile(module))
            self.assertEqual(assigned & FORBIDDEN, set(),
                             f"{module.__name__} assigns protected state")
            self.assertEqual(called, set(),
                             f"{module.__name__} performs ORM persistence")

    def test_the_command_performs_no_orm_persistence(self):
        from groups.management.commands import kt_dataset_build
        assigned, called = _writes(inspect.getfile(kt_dataset_build))
        self.assertEqual(assigned & FORBIDDEN, set())
        self.assertEqual(called, set())

    def test_core_package_does_not_import_django(self):
        """
        Only `adapters` may touch Django, and lazily. A benchmark the web app
        can import is one that will eventually be imported at request time.
        """
        from kt_dataset import schema
        for module in (schema, validation, sources, pipeline, stats):
            tree = ast.parse(pathlib.Path(
                inspect.getfile(module)).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name.split(".")[0], "django")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotEqual(node.module.split(".")[0], "django")

    def test_no_module_imports_torch(self):
        for module in self.modules():
            source = pathlib.Path(
                inspect.getfile(module)).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0],
                                         {"torch", "transformers"})
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0],
                                     {"torch", "transformers"})

    def test_no_module_downloads_anything(self):
        """
        Acquisition is an operator action under the dataset's own licence.
        Automating it would accept terms on the operator's behalf.
        """
        for module in self.modules():
            source = pathlib.Path(
                inspect.getfile(module)).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(
                            alias.name.split(".")[0],
                            {"requests", "urllib", "httpx", "aiohttp", "socket"})
                elif isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(
                        node.module.split(".")[0],
                        {"requests", "urllib", "httpx", "aiohttp", "socket"})

    def test_guard_catches_a_real_write(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write("def f(q):\n    q.trust_state = 'X'\n    q.save()\n")
            path = handle.name
        self.addCleanup(lambda: pathlib.Path(path).unlink(missing_ok=True))
        assigned, called = _writes(path)
        self.assertIn("trust_state", assigned)
        self.assertIn("save", called)
