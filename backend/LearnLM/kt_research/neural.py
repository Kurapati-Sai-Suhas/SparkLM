"""
DKT and the Transformer baseline (M2 P2.12 §22D, §22E).

Two sequence models over the same corpus, the same split and the same scored
rows as BKT, so the three numbers in the comparison table differ because the
models differ and for no other reason.

── Causality is structural here, not a mask that has to be right ───────────

Every model in this file consumes a SHIFTED input sequence:

    position      0       1        2        3
    scored        c0      c1       c2       c3
    fed in       BOS   (c0,y0)  (c1,y1)  (c2,y2)

The label `y_t` is fed at step t+1 and never at step t. A model predicting
position t therefore cannot see its own answer even if the attention mask
were wrong — the information is not in the tensor. Getting this from a mask
alone is the commonest way a knowledge-tracing result turns out to be
nonsense, and a mask is one sign flip away from silently leaking.

Position 0 is still scored: the model sees BOS plus the identity of the item
being asked, which is the neural counterpart of BKT's prior. All three models
therefore score exactly the same rows.

── Two things that are NOT in this file, deliberately ──────────────────────

§22E stops at the plain encoder. There is no temporal gating, no elapsed
time, no response time, no prerequisite signal. Positional information is
ORDER ONLY — a learned position embedding, not a clock. That is what makes
this a control: the next phase's additions have something to be measured
against, and a baseline that quietly included half of them would make its
own successor look free.

── The context window ──────────────────────────────────────────────────────

BKT carries state over a learner's whole history; these models see at most
`max_length` prior interactions. That is an inherent property of a fixed
context, not an evaluation asymmetry, and it is reported. What WOULD be an
artefact is a chunk boundary landing mid-history and scoring a position with
no context at all, so evaluation uses overlapping windows: every scored
position gets either its entire history or at least `max_length // 2` of it.
"""

import json
import math
import pathlib
import random

import torch
from torch import nn

from kt_research import models as zoo

#: Reserved concept index for anything unseen during training.
UNKNOWN_CONCEPT = "<unk>"

# ═════════════════════════════════════════════════════════════
# Temporal features (M2 P2.13 §23B, §23C)
# ═════════════════════════════════════════════════════════════

#: The four temporal numbers, and which side of the prediction each is on.
#:
#: THE RULE, restated because everything here depends on it: a feature may
#: inform position t only if it is knowable BEFORE the learner answers item t.
#:
#:   prev_log_duration   PAST  — how long the learner took over the PREVIOUS
#:                       item. Reading the current one would be reading most
#:                       of the answer: ninety seconds means they struggled.
#:   prev_duration_missing
#:                       PAST  — flag. A missing duration is not a zero.
#:   query_log_attempts  QUERY — prior attempts by this learner on THIS
#:                       question, counted by the corpus build from history.
#:   query_log_gap       QUERY — ordinal distance from the previous
#:                       interaction. See the warning below.
#:
#: ── What `query_log_gap` is NOT ─────────────────────────────────────────
#:
#: It is NOT elapsed time, and it is never called that. ASSISTments 2009 has
#: no clock; the gap counts how many OTHER logged interactions, by anyone,
#: fell between this learner's two. It correlates with real elapsed time and
#: also with how busy the platform was that day, and there is no way to
#: separate the two from this corpus. It is included as the strongest
#: LEGITIMATE ordering signal available, and any result that leans on it
#: should be read with that confound in mind.
TEMPORAL_FEATURES = ("prev_log_duration", "prev_duration_missing",
                     "query_log_attempts", "query_log_gap")

#: Indices into a temporal row that are continuous and get standardised.
#: `prev_duration_missing` is a flag and is left at 0/1.
SCALED_FEATURES = (0, 2, 3)


def temporal_rows(rows):
    """
    Per-position temporal features for one learner sequence.

    Returns a list of lists, aligned with `rows`. Position 0 reports a
    missing previous duration and a zero gap, because it genuinely has no
    predecessor — that is the cold-start case, not a defect.
    """
    matrix = []
    for position, row in enumerate(rows):
        if position == 0:
            previous_duration, missing = 0.0, 1.0
            gap = 0.0
        else:
            earlier = rows[position - 1]
            duration = earlier.get("response_time_ms")
            if duration is None:
                previous_duration, missing = 0.0, 1.0
            else:
                previous_duration = math.log1p(max(0.0, duration) / 1000.0)
                missing = 0.0
            gap = max(0.0, float(row.get("timestamp", 0))
                      - float(earlier.get("timestamp", 0)))

        matrix.append([
            previous_duration,
            missing,
            math.log1p(max(0, int(row.get("attempt_number", 0) or 0))),
            math.log1p(gap),
        ])
    return matrix


class FeatureScaler:
    """
    Standardises the continuous temporal features.

    Fitted on TRAINING sequences only. Computing a mean over the whole
    corpus would carry test-set information into a training feature — a
    small leak, but the kind that is invisible in every metric and
    impossible to argue away afterwards.
    """

    def __init__(self, means=None, deviations=None):
        self.means = list(means or [])
        self.deviations = list(deviations or [])

    def fit(self, sequences):
        columns = {index: [] for index in SCALED_FEATURES}
        for rows in sequences.values():
            for features in temporal_rows(rows):
                for index in SCALED_FEATURES:
                    columns[index].append(features[index])

        self.means, self.deviations = [], []
        for index in range(len(TEMPORAL_FEATURES)):
            values = columns.get(index)
            if not values:
                self.means.append(0.0)
                self.deviations.append(1.0)
                continue
            mean = sum(values) / len(values)
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            # A constant column has zero variance; dividing by it would make
            # every value NaN and the whole model silently untrainable.
            self.means.append(mean)
            self.deviations.append(math.sqrt(variance) or 1.0)
        return self

    def transform(self, features):
        return [
            ((value - self.means[index]) / self.deviations[index])
            if index in SCALED_FEATURES else value
            for index, value in enumerate(features)]

    def as_dict(self):
        return {"means": self.means, "deviations": self.deviations}

    @classmethod
    def from_dict(cls, payload):
        return cls(payload["means"], payload["deviations"])


class Vocabulary:
    """
    Concept -> index, built from TRAINING ROWS ONLY.

    Fitting the vocabulary on the whole corpus is a small leak with a large
    consequence: the model gets an embedding slot for an item it is about to
    be tested on and was never taught, and the test set stops measuring what
    happens when something new arrives. Deployment always has unseen items,
    so `UNKNOWN_CONCEPT` is a real case rather than a defensive branch.
    """

    def __init__(self, concepts):
        ordered = sorted({str(c) for c in concepts})
        self.index = {name: position for position, name in enumerate(ordered)}
        self.unknown = len(ordered)
        self.size = len(ordered) + 1

    @classmethod
    def from_sequences(cls, sequences):
        return cls(zoo.concept_of(row)
                   for rows in sequences.values() for row in rows)

    @classmethod
    def of_questions(cls, sequences):
        return cls(row.get("question_id", "") for rows in sequences.values()
                   for row in rows)

    def encode(self, row):
        return self.index.get(str(zoo.concept_of(row)), self.unknown)

    def encode_question(self, row):
        return self.index.get(str(row.get("question_id", "")), self.unknown)

    def as_dict(self):
        return {"index": self.index, "unknown": self.unknown,
                "size": self.size}

    @classmethod
    def from_dict(cls, payload):
        vocabulary = cls([])
        vocabulary.index = dict(payload["index"])
        vocabulary.unknown = payload["unknown"]
        vocabulary.size = payload["size"]
        return vocabulary


def seed_everything(seed):
    """One seed for every source of randomness the run touches."""
    random.seed(seed)
    torch.manual_seed(seed)


def training_chunks(length, max_length):
    """Non-overlapping [start, end) chunks. Each position trained on once."""
    return [(start, min(start + max_length, length))
            for start in range(0, length, max_length)]


def scoring_windows(length, max_length):
    """
    Overlapping (start, end, score_from) windows covering every position.

    `score_from` is the first position in the window that this window is
    responsible for scoring; everything before it is context carried over
    from the previous window. Guarantees each scored position sees at least
    `max_length // 2` prior interactions, or its whole history if shorter.
    """
    if length <= max_length:
        return [(0, length, 0)]

    carry = max_length // 2
    windows = [(0, max_length, 0)]
    end = max_length
    while end < length:
        start = end - carry
        new_end = min(start + max_length, length)
        windows.append((start, new_end, end))
        end = new_end
    return windows


class _SequenceModel:
    """
    What DKT and the Transformer share: encoding, training loop, scoring,
    checkpointing. Only `_forward` differs, so the two results cannot drift
    apart for any reason other than the architecture.
    """

    name = "sequence-model"
    checkpoint_suffix = ".pt"

    #: Ablation switches (M2 P2.13 §23E). Subclasses flip exactly one each,
    #: so a rung cannot differ from the one below it in any way the table
    #: does not name.
    use_question = False
    use_temporal = False
    use_gate = False

    def __init__(self, *, hidden=64, embedding=64, max_length=200,
                 epochs=15, batch_size=32, learning_rate=1e-3, dropout=0.2,
                 seed=20260827, patience=3, device="cpu"):
        self.hyperparameters = {
            "hidden": hidden, "embedding": embedding,
            "max_length": max_length, "epochs": epochs,
            "batch_size": batch_size, "learning_rate": learning_rate,
            "dropout": dropout, "seed": seed, "patience": patience,
        }
        self.max_length = max_length
        self.epochs = epochs
        self.batch_size = batch_size
        self.learning_rate = learning_rate
        self.patience = patience
        self.seed = seed
        self.device = torch.device(device)
        self.vocabulary = None
        self.questions = None
        self.scaler = None
        self.network = None
        self.history = []
        self.training_seconds = None
        self._fitted = False

    @property
    def parameter_count(self):
        if self.network is None:
            return 0
        return sum(p.numel() for p in self.network.parameters())

    @property
    def components(self):
        """What this rung actually switches on. Reported with every result."""
        return {"question_embedding": self.use_question,
                "temporal_features": self.use_temporal,
                "gated_fusion": self.use_gate}

    # ── encoding ──────────────────────────────────────────────────────

    def _encode(self, rows):
        concepts = [self.vocabulary.encode(row) for row in rows]
        labels = [int(bool(row["correct"])) for row in rows]
        questions = ([self.questions.encode_question(row) for row in rows]
                     if self.questions is not None else [0] * len(rows))
        temporal = ([self.scaler.transform(features)
                     for features in temporal_rows(rows)]
                    if self.scaler is not None
                    else [[0.0] * len(TEMPORAL_FEATURES)] * len(rows))
        return concepts, labels, questions, temporal

    def _tensors(self, chunks):
        """
        Pad encoded chunks into batched tensors.

        Index 0 of every id tensor is reserved for padding, so the real
        values are shifted by one and a padded step can never be confused
        with concept 0 — an off-by-one here silently trains the model on a
        phantom skill.

        The SHIFT is the causal contract, and it is applied to the temporal
        features exactly as it is to the label: `past` at step t carries the
        duration of interaction t-1, `query` at step t carries only what is
        knowable before item t is answered.
        """
        width = max(len(chunk[0]) for chunk in chunks)
        size = len(chunks)
        vocabulary_size = self.vocabulary.size

        inputs = torch.zeros(size, width, dtype=torch.long)
        queries = torch.zeros(size, width, dtype=torch.long)
        previous_questions = torch.zeros(size, width, dtype=torch.long)
        query_questions = torch.zeros(size, width, dtype=torch.long)
        past_features = torch.zeros(size, width, 2, dtype=torch.float)
        query_features = torch.zeros(size, width, 2, dtype=torch.float)
        targets = torch.zeros(size, width, dtype=torch.float)
        mask = torch.zeros(size, width, dtype=torch.bool)

        for number, (concepts, labels, questions, temporal) in enumerate(chunks):
            for position, (concept, label) in enumerate(zip(concepts, labels)):
                queries[number, position] = concept + 1
                query_questions[number, position] = questions[position] + 1
                targets[number, position] = float(label)
                mask[number, position] = True

                features = temporal[position]
                past_features[number, position, 0] = features[0]
                past_features[number, position, 1] = features[1]
                query_features[number, position, 0] = features[2]
                query_features[number, position, 1] = features[3]

                if position == 0:
                    # BOS: no interaction has happened yet.
                    inputs[number, position] = 2 * vocabulary_size + 1
                    previous_questions[number, position] = 0
                else:
                    previous = concepts[position - 1]
                    previous_label = labels[position - 1]
                    inputs[number, position] = previous * 2 + previous_label + 1
                    previous_questions[number, position] = (
                        questions[position - 1] + 1)

        move = lambda tensor: tensor.to(self.device)      # noqa: E731
        return (move(inputs), move(queries), move(previous_questions),
                move(query_questions), move(past_features),
                move(query_features), move(targets), move(mask))

    # ── the network ───────────────────────────────────────────────────

    def _build_network(self):
        raise NotImplementedError

    def _forward(self, *tensors):
        """Logits for every position. Shape (batch, width)."""
        raise NotImplementedError

    # ── fitting ───────────────────────────────────────────────────────

    def fit(self, sequences, validation=None):
        """
        `sequences` maps learner -> their TRAINING rows, in order.

        `validation` is a list of (full_sequence, scored_indices) pairs. The
        sequence carries the learner's earlier history as context and the
        indices name the rows being scored, so early stopping is measured the
        same causal way the test set is — a model selected on a differently
        constructed metric is tuned for a task nobody reports.
        """
        import time

        started = time.monotonic()
        seed_everything(self.seed)
        self.vocabulary = Vocabulary.from_sequences(sequences)
        # Question vocabulary and feature scaling are fitted on TRAINING rows
        # only. A vocabulary or a mean taken over the whole corpus carries
        # test-set information into a training feature.
        if self.use_question:
            self.questions = Vocabulary.of_questions(sequences)
        if self.use_temporal:
            self.scaler = FeatureScaler().fit(sequences)
        self.network = self._build_network().to(self.device)

        chunks = []
        for learner in sorted(sequences):
            rows = sequences[learner]
            concepts, labels, questions, temporal = self._encode(rows)
            for start, end in training_chunks(len(rows), self.max_length):
                if end - start >= 2:
                    chunks.append((concepts[start:end], labels[start:end],
                                   questions[start:end], temporal[start:end]))

        if not chunks:
            raise ValueError(
                "no training sequence is long enough to fit; a model cannot "
                "be trained on single-interaction histories")

        optimiser = torch.optim.Adam(self.network.parameters(),
                                     lr=self.learning_rate)
        loss_function = nn.BCEWithLogitsLoss(reduction="none")

        best_auc, best_state, worse_epochs = float("-inf"), None, 0
        generator = random.Random(self.seed)

        for epoch in range(1, self.epochs + 1):
            self.network.train()
            order = list(range(len(chunks)))
            generator.shuffle(order)

            total_loss, counted = 0.0, 0
            for offset in range(0, len(order), self.batch_size):
                batch = [chunks[i] for i in order[offset:offset + self.batch_size]]
                *features, targets, mask = self._tensors(batch)

                optimiser.zero_grad()
                logits = self._forward(*features)
                losses = loss_function(logits, targets) * mask
                loss = losses.sum() / mask.sum().clamp(min=1)
                loss.backward()
                nn.utils.clip_grad_norm_(self.network.parameters(), 5.0)
                optimiser.step()

                total_loss += float(losses.sum().detach())
                counted += int(mask.sum())

            record = {"epoch": epoch,
                      "train_loss": round(total_loss / max(counted, 1), 6)}

            if validation:
                self._fitted = True
                labels, scores = score_tasks(self, validation)
                record["validation_auc"] = round(zoo.auc(labels, scores), 6)
                record["validation_log_loss"] = round(
                    zoo.log_loss(labels, scores), 6)

                if record["validation_auc"] > best_auc:
                    best_auc = record["validation_auc"]
                    best_state = {k: v.detach().clone() for k, v
                                  in self.network.state_dict().items()}
                    worse_epochs = 0
                else:
                    worse_epochs += 1

            self.history.append(record)
            if validation and worse_epochs >= self.patience:
                record["stopped_early"] = True
                break

        if best_state is not None:
            # Restore the epoch validation chose, not the last one trained.
            self.network.load_state_dict(best_state)
        self._fitted = True
        self.training_seconds = round(time.monotonic() - started, 2)
        return self

    # ── prediction ────────────────────────────────────────────────────

    def predict_sequence(self, rows):
        """
        P(correct) for every position, using ONLY earlier interactions.

        Same contract as `BKT.predict_sequence`, so the runner scores all
        three models through one code path.
        """
        if not self._fitted:
            raise zoo.NotTrainedError("fit() before predict_sequence()")
        if not rows:
            return []

        concepts, labels, questions, temporal = self._encode(rows)
        predictions = [None] * len(rows)

        self.network.eval()
        with torch.no_grad():
            for start, end, score_from in scoring_windows(len(rows),
                                                          self.max_length):
                window = (concepts[start:end], labels[start:end],
                          questions[start:end], temporal[start:end])
                *features, _targets, _mask = self._tensors([window])
                probabilities = torch.sigmoid(self._forward(*features))[0]
                for position in range(score_from, end):
                    predictions[position] = float(
                        probabilities[position - start])

        missing = [i for i, value in enumerate(predictions) if value is None]
        if missing:
            raise RuntimeError(
                f"{len(missing)} positions were never scored; the scoring "
                f"windows do not cover the sequence")
        return predictions

    # ── On batching the scoring path ──────────────────────────────────
    #
    # P2.12 recorded "batch the scoring path" as the obvious next
    # improvement, on the assumption that per-learner forward passes
    # dominated the wall clock. MEASURED IN P2.13, THAT WAS WRONG: batching
    # 400 real sequences ran 1.2x faster, not the 5-10x assumed, because the
    # cost is the encoder arithmetic itself rather than the per-call
    # overhead.
    #
    # And it is not free. Padding changes the reduction order inside the
    # matmuls, so predictions moved by up to 2.4e-07 and test AUC moved in
    # the seventh decimal — enough to break the bit-identical reproduction
    # of the frozen P2.12 baseline that §23A requires. A 1.2x speedup is not
    # worth paying for in reproducibility, so scoring stays one sequence at
    # a time and this note stands in place of the optimisation.

    # ── checkpointing ─────────────────────────────────────────────────

    def save(self, path):
        """Weights, vocabulary and hyperparameters — everything to re-score."""
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model": self.name,
            "hyperparameters": self.hyperparameters,
            "vocabulary": self.vocabulary.as_dict(),
            # Without these the checkpoint scores differently from the run
            # that produced it: a different question vocabulary maps the same
            # item to a different embedding, and a different scaler feeds the
            # same duration in as a different number.
            "questions": (self.questions.as_dict()
                          if self.questions is not None else None),
            "scaler": (self.scaler.as_dict()
                       if self.scaler is not None else None),
            "components": self.components,
            "state_dict": self.network.state_dict(),
            "history": self.history,
            "training_seconds": self.training_seconds,
        }, path)
        return str(path)

    def load(self, path):
        payload = torch.load(pathlib.Path(path), map_location=self.device,
                             weights_only=False)
        if payload["model"] != self.name:
            raise ValueError(
                f"checkpoint holds {payload['model']!r}, not {self.name!r}")
        stored = payload.get("components")
        if stored is not None and stored != self.components:
            raise ValueError(
                f"checkpoint was trained with components {stored} but this "
                f"model has {self.components}; loading it would score one "
                f"architecture's weights through another")
        self.hyperparameters = payload["hyperparameters"]
        # Re-sync the attributes that are read directly rather than through
        # `hyperparameters`. `max_length` is the one that matters: it sets
        # the scoring window, so a checkpoint trained with a short context
        # would otherwise be scored with the default long one and quietly
        # report numbers from a model that never existed.
        self.max_length = self.hyperparameters.get("max_length",
                                                   self.max_length)
        self.batch_size = self.hyperparameters.get("batch_size",
                                                   self.batch_size)
        self.vocabulary = Vocabulary.from_dict(payload["vocabulary"])
        self.questions = (Vocabulary.from_dict(payload["questions"])
                          if payload.get("questions") else None)
        self.scaler = (FeatureScaler.from_dict(payload["scaler"])
                       if payload.get("scaler") else None)
        self.network = self._build_network().to(self.device)
        self.network.load_state_dict(payload["state_dict"])
        self.history = payload.get("history", [])
        self.training_seconds = payload.get("training_seconds")
        self._fitted = True
        return self


# ═════════════════════════════════════════════════════════════
# DKT
# ═════════════════════════════════════════════════════════════

class _DKTNetwork(nn.Module):
    def __init__(self, interactions, concepts, embedding, hidden, dropout):
        super().__init__()
        self.embedding = nn.Embedding(interactions, embedding, padding_idx=0)
        self.lstm = nn.LSTM(embedding, hidden, batch_first=True)
        self.dropout = nn.Dropout(dropout)
        self.output = nn.Linear(hidden, concepts)

    def forward(self, inputs, queries):
        hidden, _state = self.lstm(self.embedding(inputs))
        logits = self.output(self.dropout(hidden))
        # One output unit per concept; read off the unit for the item ACTUALLY
        # asked at this position. Queries are +1 shifted for padding.
        return logits.gather(2, (queries - 1).clamp(min=0)
                             .unsqueeze(-1)).squeeze(-1)


class DKT(_SequenceModel):
    """
    Deep Knowledge Tracing (Piech et al., 2015): an LSTM over the interaction
    sequence with one output unit per concept.

    The reference implementation, and the rung the Transformer has to beat to
    justify its cost.
    """

    name = "DKT"

    def _build_network(self):
        return _DKTNetwork(
            interactions=2 * self.vocabulary.size + 2,
            concepts=self.vocabulary.size,
            embedding=self.hyperparameters["embedding"],
            hidden=self.hyperparameters["hidden"],
            dropout=self.hyperparameters["dropout"])

    def _forward(self, inputs, queries, *unused):
        # DKT consumes concept and correctness and nothing else. The unused
        # tensors are named here rather than silently dropped, so it is
        # obvious that this rung is not quietly reading the P2.13 features.
        return self.network(inputs, queries)


# ═════════════════════════════════════════════════════════════
# Transformer baseline
# ═════════════════════════════════════════════════════════════

class _TransformerNetwork(nn.Module):
    """
    One network for all four ablation rungs, switched by flags.

    ── Why the module creation ORDER is load-bearing ───────────────────────

    torch draws from the global RNG as each module is constructed, so
    creating anything NEW before the P2.12 modules would change their
    initial weights and the frozen baseline would stop reproducing. Every
    P2.13 module is therefore APPENDED, never inserted, and with all flags
    off this class is bit-for-bit the P2.12 network. A test asserts the
    baseline still reproduces its recorded numbers.

    Widths are constant across rungs by construction: query-side signals are
    SUMMED into one d-dimensional vector rather than concatenated, so the
    head has the same shape in every rung and the parameter differences
    between rungs are only the new tables themselves.
    """

    def __init__(self, interactions, concepts, embedding, hidden, dropout,
                 heads, layers, max_length, questions=0,
                 use_question=False, use_temporal=False, use_gate=False):
        super().__init__()
        self.interaction_embedding = nn.Embedding(interactions, embedding,
                                                  padding_idx=0)
        self.concept_embedding = nn.Embedding(concepts + 1, embedding,
                                              padding_idx=0)
        # ORDER, not time. A learned position embedding says "this came
        # third"; it says nothing about how long the learner waited.
        self.position_embedding = nn.Embedding(max_length, embedding)
        self.dropout = nn.Dropout(dropout)

        layer = nn.TransformerEncoderLayer(
            d_model=embedding, nhead=heads, dim_feedforward=hidden,
            dropout=dropout, batch_first=True, activation="gelu")
        self.encoder = nn.TransformerEncoder(layer, num_layers=layers)

        # The head sees the encoded history AND the identity of the item being
        # asked. Without the query the model would be predicting "does this
        # learner get the next thing right", which is a different question and
        # an easier one.
        self.head = nn.Sequential(
            nn.Linear(embedding * 2, hidden), nn.GELU(),
            nn.Dropout(dropout), nn.Linear(hidden, 1))

        # ── appended for P2.13; nothing above this line moved ────────────
        self.use_question = use_question
        self.use_temporal = use_temporal
        self.use_gate = use_gate

        if use_question:
            self.question_embedding = nn.Embedding(questions + 1, embedding,
                                                   padding_idx=0)
        if use_temporal:
            # Two projections, not one. The past-side features (how long the
            # LAST answer took) and the query-side ones (attempts on THIS
            # item, ordinal gap) describe different things and are consumed
            # at different points; sharing weights would force one linear map
            # to mean both.
            self.past_temporal = nn.Linear(2, embedding)
            self.query_temporal = nn.Linear(2, embedding)
        if use_gate:
            # A learned, bounded gate: sigmoid per dimension, so the model
            # can keep the temporal channel where it helps and shut it off
            # where it does not — per feature dimension, not globally.
            self.gate = nn.Linear(embedding * 2, embedding)

    def forward(self, inputs, queries, previous_questions, query_questions,
                past_features, query_features, return_gate=False):
        width = inputs.size(1)
        positions = torch.arange(width, device=inputs.device).unsqueeze(0)

        content = self.interaction_embedding(inputs)
        if self.use_question:
            content = content + self.question_embedding(previous_questions)

        gate = None
        if self.use_temporal:
            temporal = self.past_temporal(past_features)
            if self.use_gate:
                gate = torch.sigmoid(
                    self.gate(torch.cat([content, temporal], dim=-1)))
                content = gate * content + (1.0 - gate) * temporal
            else:
                # The no-gate control: fusion by plain addition, so the only
                # difference from the rung above is whether the mixture is
                # learned.
                content = content + temporal

        hidden = self.dropout(content + self.position_embedding(positions))
        causal = nn.Transformer.generate_square_subsequent_mask(
            width, device=inputs.device)
        encoded = self.encoder(hidden, mask=causal, is_causal=True)

        query = self.concept_embedding(queries)
        if self.use_question:
            query = query + self.question_embedding(query_questions)
        if self.use_temporal:
            query = query + self.query_temporal(query_features)

        logits = self.head(torch.cat([encoded, query], dim=-1)).squeeze(-1)
        return (logits, gate) if return_gate else logits


class TransformerKT(_SequenceModel):
    """
    The smallest correct Transformer knowledge tracer (§22E).

        embeddings -> positional ordering -> causal encoder
                   -> prediction head -> P(next response correct)

    No temporal gating. No prerequisite graph. No elapsed-time features.
    Those are the next phase's, and their whole claim is measured against
    this.
    """

    name = "Transformer"

    def __init__(self, *, heads=4, layers=2, **parameters):
        super().__init__(**parameters)
        self.hyperparameters.update({"heads": heads, "layers": layers})

    def _build_network(self):
        return _TransformerNetwork(
            interactions=2 * self.vocabulary.size + 2,
            concepts=self.vocabulary.size,
            questions=self.questions.size if self.questions else 0,
            embedding=self.hyperparameters["embedding"],
            hidden=self.hyperparameters["hidden"],
            dropout=self.hyperparameters["dropout"],
            heads=self.hyperparameters["heads"],
            layers=self.hyperparameters["layers"],
            max_length=self.hyperparameters["max_length"],
            use_question=self.use_question,
            use_temporal=self.use_temporal,
            use_gate=self.use_gate)

    def _forward(self, *tensors):
        return self.network(*tensors)

    def mean_gate(self, sequences, limit=200):
        """
        The average value of the learned gate, for reporting (§23G).

        A gate pinned near 1 means the temporal channel was learned away and
        the model is the rung below it wearing extra parameters. That is
        worth knowing before anyone reads a difference in AUC as evidence
        the mechanism did something.
        """
        if not self.use_gate:
            return None

        total, counted = 0.0, 0
        self.network.eval()
        with torch.no_grad():
            for rows in list(sequences)[:limit]:
                if not rows:
                    continue
                encoded = self._encode(rows[:self.max_length])
                *features, _targets, mask = self._tensors([encoded])
                _logits, gate = self.network(*features, return_gate=True)
                selected = gate[0][mask[0]]
                total += float(selected.sum())
                counted += selected.numel()
        return round(total / counted, 6) if counted else None


# ═════════════════════════════════════════════════════════════
# The P2.13 ablation ladder (§23E)
# ═════════════════════════════════════════════════════════════

class TransformerTemporal(TransformerKT):
    """
    Rung 2 — the frozen baseline plus response time. Additive fusion.

    Response time only: no question embedding, so the step up from the
    baseline changes exactly one thing.
    """

    name = "Transformer+T"
    use_temporal = True


class TransformerGated(TransformerTemporal):
    """
    Rung 3 — the same temporal features, mixed by a LEARNED gate instead of
    plain addition.

    The step that isolates the gate. Whatever separates this from the rung
    below it is the gating mechanism and nothing else, which is the only way
    to answer "did gating do anything" rather than "did the bigger model win".
    """

    name = "Transformer+TG"
    use_gate = True


class TAGTKT(TransformerGated):
    """
    Rung 4 — the full architecture: question, concept, interaction and
    response-time representations, gated.

    TA-GTKT is a NAME FOR THIS CONFIGURATION, not a claim of novelty.
    Temporal encoding and gated fusion both appear in the knowledge-tracing
    literature; what this ladder can honestly claim is a controlled
    comparison of them on one corpus with one split.

    The question embedding is the single largest block of parameters in the
    model — 17,751 training questions against 112 concepts — and most of
    those items are seen a handful of times. Adding it LAST, on its own
    step, is what makes it possible to tell a gain from the gate apart from
    a loss to an over-parameterised embedding table.
    """

    name = "TA-GTKT"
    use_question = True


# ═════════════════════════════════════════════════════════════
# Scoring
# ═════════════════════════════════════════════════════════════

def score_tasks(model, tasks):
    """
    (labels, scores) for a list of (sequence, scored_indices) pairs.

    The one scoring path, used by every model and by early stopping, so a
    metric can never differ because of how it was collected.
    """
    labels, scores = [], []
    for sequence, indices in tasks:
        predictions = model.predict_sequence(sequence)
        for position in indices:
            labels.append(bool(sequence[position]["correct"]))
            scores.append(predictions[position])
    return labels, scores


def describe_environment():
    return {"torch": torch.__version__,
            "device": "cpu",
            "threads": torch.get_num_threads()}


def write_history(path, history):
    pathlib.Path(path).write_text(
        json.dumps(history, indent=2) + "\n", encoding="utf-8")
