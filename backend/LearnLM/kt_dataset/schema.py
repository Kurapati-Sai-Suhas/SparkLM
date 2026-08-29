"""
Canonical KT interaction schema (M2 P2.10b).

One record shape that every source — public benchmark or LearnLM — is mapped
into, so downstream code never branches on where the data came from. Pure
Python: no Django, no numpy, no pandas.

── Ordinal time, not wall-clock time ──────────────────────────────────────

The field is `sequence_position`, not `timestamp`, and that is a finding
rather than a preference.

ASSISTments 2009 has **no absolute timestamp column at all**. Its columns are
`order_id` (an ordering key), `ms_first_response` and `overlap_time` — both
DURATIONS in milliseconds, not points in time. Sources also disagree on
whether `order_id` is strictly chronological: the EduData schema reference
calls it a "non-chronological record identifier", while the common convention
in the KT literature is to sort by it as a chronological proxy.

So the canonical record carries:

  * `sequence_position` — a monotone ordinal within a learner's history. Always
    available, and the only thing the split is allowed to order by.
  * `occurred_at` — a real wall-clock timestamp, or None when the source has
    none. **Never synthesised.**

`lag_seconds` is therefore None for any source without `occurred_at`. That
kills SAINT+'s lag-time feature on ASSISTments 2009 specifically, which is
worth knowing before a phase is spent trying to reproduce it.

── Provenance travels with every row ──────────────────────────────────────

`dataset_name`, `dataset_version`, `source_file`, `source_row_id` and
`raw_dataset_hash` are on the record itself, not only in the manifest. A row
that gets separated from its manifest is still traceable to the exact file and
line it came from — the same argument P2.7g-1 made for output provenance.
"""

from dataclasses import asdict, dataclass, field

#: Bumped when the canonical field set or its semantics change. Participates in
#: the processed hash, so a schema change cannot silently reuse a cached build.
#:
#: v2 (M2 P2.13) adds `response_time_ms`. ASSISTments 2009 carries a genuine
#: response duration in `ms_first_response` that v1 simply did not transport,
#: so a temporal model built on v1 would have had to invent one. Adding a
#: column changes the PROCESSED hash of every corpus and changes the SPLIT
#: hash of none: the partition is a function of learner and position, and
#: neither moved. A test asserts exactly that.
SCHEMA_VERSION = 2

#: Sentinel for "this source cannot supply this field".
#:
#: Distinct from a missing value in a row that should have had one: UNAVAILABLE
#: is a property of the SOURCE, absence is a property of the ROW. Collapsing
#: them would make "ASSISTments has no timestamps" indistinguishable from
#: "this particular interaction lost its timestamp", and only the second is a
#: data-quality problem.
UNAVAILABLE = None


@dataclass(frozen=True)
class Provenance:
    """Where a row came from. Immutable, and carried per record."""
    dataset_name: str
    dataset_version: str
    source_file: str
    raw_dataset_hash: str
    schema_version: int = SCHEMA_VERSION

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Interaction:
    """
    One learner attempt at one item.

    Frozen: a record is a statement about something that already happened, and
    the pipeline's determinism guarantee depends on records not being edited
    in place between the hash and the split.
    """

    learner_id: str
    question_id: str
    concept_id: str                 # "" when the source has no concept mapping
    sequence_position: int          # monotone within this learner
    correct: int                    # 1 / 0 — the label
    attempt_number: int             # PRIOR attempts on this (learner, question)

    source_row_id: str = ""
    occurred_at: object = UNAVAILABLE      # real timestamp, or None
    lag_seconds: float = UNAVAILABLE       # requires occurred_at

    #: How long the learner took to give their first response, in
    #: milliseconds, or None when the source has none (M2 P2.13).
    #:
    #: A DURATION, like `lag_seconds`, and not a point in time — it does not
    #: give this corpus a clock. Carried verbatim from the source apart from
    #: impossible values: a negative duration is dropped to None, because 8
    #: rows of the published ASSISTments file are negative and that is a
    #: source defect, not a fast answer.
    #:
    #: NOT capped here, deliberately. The longest value in ASSISTments 2009
    #: is 23 hours, which is a session left open rather than a response, but
    #: deciding what to do about that is a MODELLING choice. A corpus that
    #: silently squashed it would be asserting a value nobody measured.
    #:
    #: Knowable only AFTER the learner answers. Any model using it must feed
    #: it as evidence about a PAST interaction; using it at the position it
    #: describes would be reading the answer sheet.
    response_time_ms: float = UNAVAILABLE

    outcome_label: str = ""                # source's own verdict vocabulary
    provenance: Provenance = None

    #: Point-in-time rating state, when a source can supply it HONESTLY.
    #:
    #: Present as columns so LearnLM can start logging them at interaction time
    #: (P2.10a recorded that it currently cannot: LearnerTopicSkill stores only
    #: current state, and Glicko-2 updates in rating periods whose boundaries
    #: are not recorded, so replay reconstructs a plausible history rather than
    #: the actual one). Until a source can fill these, they stay None — never
    #: back-filled from present-day values.
    glicko_rating_at_time: float = UNAVAILABLE
    glicko_rd_at_time: float = UNAVAILABLE

    def as_row(self):
        """Flat dict for CSV writing. Column order is CANONICAL_COLUMNS."""
        return {
            "learner_id": self.learner_id,
            "question_id": self.question_id,
            "concept_id": self.concept_id,
            "sequence_position": self.sequence_position,
            "correct": self.correct,
            "attempt_number": self.attempt_number,
            "source_row_id": self.source_row_id,
            "occurred_at": ("" if self.occurred_at is None
                            else self.occurred_at.isoformat()),
            "lag_seconds": ("" if self.lag_seconds is None
                            else f"{self.lag_seconds:.3f}"),
            "response_time_ms": ("" if self.response_time_ms is None
                                 else f"{self.response_time_ms:.0f}"),
            "outcome_label": self.outcome_label,
            "dataset_name": self.provenance.dataset_name if self.provenance else "",
            "dataset_version": (self.provenance.dataset_version
                                if self.provenance else ""),
            "source_file": self.provenance.source_file if self.provenance else "",
            "raw_dataset_hash": (self.provenance.raw_dataset_hash
                                 if self.provenance else ""),
            "schema_version": (self.provenance.schema_version
                               if self.provenance else SCHEMA_VERSION),
            "glicko_rating_at_time": ("" if self.glicko_rating_at_time is None
                                      else self.glicko_rating_at_time),
            "glicko_rd_at_time": ("" if self.glicko_rd_at_time is None
                                  else self.glicko_rd_at_time),
        }


#: Fixed column order for the processed CSV and the processed hash.
#:
#: Order is part of the reproducibility contract: two builds that produced the
#: same rows in a different column order would hash differently, so the order
#: is declared once here rather than implied by dict iteration.
CANONICAL_COLUMNS = (
    "learner_id", "question_id", "concept_id", "sequence_position",
    "correct", "attempt_number", "source_row_id", "occurred_at",
    "lag_seconds", "response_time_ms", "outcome_label", "dataset_name",
    "dataset_version", "source_file", "raw_dataset_hash", "schema_version",
    "glicko_rating_at_time", "glicko_rd_at_time",
)


@dataclass
class SourceCapabilities:
    """
    What a source can and cannot supply — declared, not discovered at runtime.

    Lets the pipeline refuse to compute a feature a source cannot support,
    instead of silently emitting nulls that a later phase mistakes for missing
    data and tries to impute.
    """
    name: str
    version: str
    has_wall_clock_time: bool
    has_concepts: bool
    has_attempt_counts: bool
    has_response_time: bool
    has_point_in_time_rating: bool
    notes: str = ""
    unavailable_reasons: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)
