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
import pathlib
import random

import torch
from torch import nn

from kt_research import models as zoo

#: Reserved concept index for anything unseen during training.
UNKNOWN_CONCEPT = "<unk>"


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

    def encode(self, row):
        return self.index.get(str(zoo.concept_of(row)), self.unknown)

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
        self.network = None
        self.history = []
        self._fitted = False

    # ── encoding ──────────────────────────────────────────────────────

    def _encode(self, rows):
        concepts = [self.vocabulary.encode(row) for row in rows]
        labels = [int(bool(row["correct"])) for row in rows]
        return concepts, labels

    def _tensors(self, chunks):
        """
        Pad a list of (concepts, labels) into batched tensors.

        Index 0 of every tensor is reserved for padding, so the real values
        are shifted by one and a padded step can never be confused with
        concept 0 — an off-by-one here silently trains the model on a
        phantom skill.
        """
        width = max(len(concepts) for concepts, _labels in chunks)
        size = len(chunks)
        vocabulary_size = self.vocabulary.size

        inputs = torch.zeros(size, width, dtype=torch.long)
        queries = torch.zeros(size, width, dtype=torch.long)
        targets = torch.zeros(size, width, dtype=torch.float)
        mask = torch.zeros(size, width, dtype=torch.bool)

        for row_number, (concepts, labels) in enumerate(chunks):
            for position, (concept, label) in enumerate(zip(concepts, labels)):
                queries[row_number, position] = concept + 1
                targets[row_number, position] = float(label)
                mask[row_number, position] = True
                if position == 0:
                    # BOS: no interaction has happened yet.
                    inputs[row_number, position] = 2 * vocabulary_size + 1
                else:
                    previous = concepts[position - 1]
                    previous_label = labels[position - 1]
                    inputs[row_number, position] = (
                        previous * 2 + previous_label + 1)

        return (inputs.to(self.device), queries.to(self.device),
                targets.to(self.device), mask.to(self.device))

    # ── the network ───────────────────────────────────────────────────

    def _build_network(self):
        raise NotImplementedError

    def _forward(self, inputs, queries):
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
        seed_everything(self.seed)
        self.vocabulary = Vocabulary.from_sequences(sequences)
        self.network = self._build_network().to(self.device)

        chunks = []
        for learner in sorted(sequences):
            rows = sequences[learner]
            concepts, labels = self._encode(rows)
            for start, end in training_chunks(len(rows), self.max_length):
                if end - start >= 2:
                    chunks.append((concepts[start:end], labels[start:end]))

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
                inputs, queries, targets, mask = self._tensors(batch)

                optimiser.zero_grad()
                logits = self._forward(inputs, queries)
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

        concepts, labels = self._encode(rows)
        predictions = [None] * len(rows)

        self.network.eval()
        with torch.no_grad():
            for start, end, score_from in scoring_windows(len(rows),
                                                          self.max_length):
                window = (concepts[start:end], labels[start:end])
                inputs, queries, _targets, _mask = self._tensors([window])
                probabilities = torch.sigmoid(
                    self._forward(inputs, queries))[0]
                for position in range(score_from, end):
                    predictions[position] = float(
                        probabilities[position - start])

        missing = [i for i, value in enumerate(predictions) if value is None]
        if missing:
            raise RuntimeError(
                f"{len(missing)} positions were never scored; the scoring "
                f"windows do not cover the sequence")
        return predictions

    # ── checkpointing ─────────────────────────────────────────────────

    def save(self, path):
        """Weights, vocabulary and hyperparameters — everything to re-score."""
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        torch.save({
            "model": self.name,
            "hyperparameters": self.hyperparameters,
            "vocabulary": self.vocabulary.as_dict(),
            "state_dict": self.network.state_dict(),
            "history": self.history,
        }, path)
        return str(path)

    def load(self, path):
        payload = torch.load(pathlib.Path(path), map_location=self.device,
                             weights_only=False)
        if payload["model"] != self.name:
            raise ValueError(
                f"checkpoint holds {payload['model']!r}, not {self.name!r}")
        self.hyperparameters = payload["hyperparameters"]
        self.vocabulary = Vocabulary.from_dict(payload["vocabulary"])
        self.network = self._build_network().to(self.device)
        self.network.load_state_dict(payload["state_dict"])
        self.history = payload.get("history", [])
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

    def _forward(self, inputs, queries):
        return self.network(inputs, queries)


# ═════════════════════════════════════════════════════════════
# Transformer baseline
# ═════════════════════════════════════════════════════════════

class _TransformerNetwork(nn.Module):
    def __init__(self, interactions, concepts, embedding, hidden, dropout,
                 heads, layers, max_length):
        super().__init__()
        self.interaction_embedding = nn.Embedding(interactions, embedding,
                                                  padding_idx=0)
        self.concept_embedding = nn.Embedding(concepts + 1, embedding,
                                              padding_idx=0)
        # ORDER, not time. A learned position embedding says "this came
        # third"; it says nothing about how long the learner waited, and
        # §22E keeps it that way.
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

    def forward(self, inputs, queries):
        width = inputs.size(1)
        positions = torch.arange(width, device=inputs.device).unsqueeze(0)
        hidden = self.dropout(self.interaction_embedding(inputs)
                              + self.position_embedding(positions))

        causal = nn.Transformer.generate_square_subsequent_mask(
            width, device=inputs.device)
        encoded = self.encoder(hidden, mask=causal, is_causal=True)

        combined = torch.cat([encoded, self.concept_embedding(queries)],
                             dim=-1)
        return self.head(combined).squeeze(-1)


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
            embedding=self.hyperparameters["embedding"],
            hidden=self.hyperparameters["hidden"],
            dropout=self.hyperparameters["dropout"],
            heads=self.hyperparameters["heads"],
            layers=self.hyperparameters["layers"],
            max_length=self.hyperparameters["max_length"])

    def _forward(self, inputs, queries):
        return self.network(inputs, queries)


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
