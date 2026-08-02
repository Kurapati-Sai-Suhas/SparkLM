# SparkLM Technical Interview Handbook
## Part 2 — Database Design, Caching & Redis

**Questions 43–84 of 254**
**Companion:** Document 04 (Database Design), Document 01 §11–12
**Previous:** Part 1 — Backend Engineering & Concurrency (Q1–42)

---

## Section D — Schema & Modelling (Q43–Q58)

---

### Q43. Walk me through your data model.

**Ideal answer.** Twenty-seven models across roughly nine families. The core is the learning
loop: `CodingPortal` contains `Topic`s connected by `TopicPrerequisite` edges forming a DAG,
and each topic has `Question`s. Against that sit the learner-state models — `UserCodingProfile`
holds a single Elo rating and counters, `UserTopicMastery` holds per-topic accuracy and review
counts, and `CodeSubmission` records every attempt. `RecommendationLog` closes the loop by
recording what the router chose, its predicted success probability, and what actually
happened. Around that
core are study groups, social connections, quizzes, notifications, and a `Document` model
carrying a 512-dimension pgvector column for similarity search.

**Why we chose this.** The separation between `UserCodingProfile` (one row per user, global
skill) and `UserTopicMastery` (one row per user-topic, local competence) is the important one.
Skill is not scalar — someone strong at arrays can be weak at graphs — but you still need a
single number for matchmaking and leaderboards.

**Alternatives.** One denormalised profile with a JSON blob of per-topic scores; a single
`UserSkill` table with a nullable topic; event sourcing with derived state.

**Tradeoffs.** A JSON blob would avoid a join but cannot be indexed or aggregated usefully —
"which topics does this cohort struggle with" becomes a full scan. Two tables cost a join and
give me real query power.

**Follow-ups.** "Why both a global rating and per-topic mastery?" · "What is `RecommendationLog`
for?" · "Would you event-source this?"

**What interviewers expect.** The reason the two-level model exists, stated as a product
constraint rather than a normalisation instinct. Also worth flagging early: per-topic
`elo_rating` exists on the mastery model but **is never updated anywhere**, which once made a
routing gate permanently unsatisfiable. Naming a vestigial field in your own schema is a good
signal.

---

### Q44. Why is `CodeSubmission` partitioned?

**Ideal answer.** It is range-partitioned by month on `submitted_at`, from day one. It is the
only table with unbounded growth — every attempt by every user forever, each carrying a full
`code` TextField. The frozen architecture calls partitioning "cheap now, painful later," and
that is exactly right: adding partitioning to a live table means a full rewrite under lock,
whereas doing it before there is data costs one migration. The payoff is that old months can be
detached and archived without touching the hot table, and time-bounded queries prune to a
single partition.

**Why we chose this.** Data lifecycle. The architecture calls for archiving `code` bodies to
object storage after 90 days; partitioning is what makes that a metadata operation instead of
a mass delete.

**Alternatives.** No partitioning with a nightly archive job; a separate cold table; TimescaleDB.

**Tradeoffs.** Partitioning imposed two real constraints I had to design around, both covered
below — the primary key had to include the partition key, and IDENTITY columns became
unavailable. It also needs ongoing maintenance to keep future partitions existing.

**Follow-ups.** "What is the partition key?" · "What happens if a row falls outside every
range?" · "How do you maintain it?"

**What interviewers expect.** That you can name the *costs*, not just the benefit. Most
candidates who mention partitioning have read about it; the constraint interactions are what
prove you implemented it.

---

### Q45. Why is the primary key `(id, submitted_at)` rather than just `id`?

**Ideal answer.** Postgres requires the partition key to be part of every unique constraint on
a partitioned table, including the primary key. Since the table is partitioned on
`submitted_at`, the PK has to be `(id, submitted_at)`. That does not weaken uniqueness of `id`
itself — `id` is backed by a sequence, so it is globally unique regardless. It is a schema
consequence of the physical layout, not a modelling decision.

**Why we chose this.** No choice. It is a Postgres rule.

**Alternatives.** Partition by `id` range instead of time (loses the time-based lifecycle);
UUID primary keys; no partitioning.

**Tradeoffs.** The composite PK means any future foreign key referencing `CodeSubmission` must
either include `submitted_at` in the reference or use application-level integrity. Nothing
references it today, and the architecture document records this explicitly as a constraint on
future tables like `CalibrationPrediction`.

**Follow-ups.** "Does that break foreign keys?" · "Is `id` still unique?" · "What would you do
if you needed an FK to it?"

**What interviewers expect.** The forward-looking consequence. A candidate who says "the PK is
composite" has read the migration; one who says "and that means any future FK to this table
needs the timestamp too, which is why the architecture doc flags it" has thought about what the
decision costs the next person.

---

### Q46. Why is `id` sequence-backed rather than an IDENTITY column?

**Ideal answer.** PostgreSQL versions before 17 forbid IDENTITY columns on partitioned tables.
So `id` uses `DEFAULT nextval(...)` from a sequence instead. Insert semantics are identical —
you omit the column and the database fills it — so nothing above the schema layer notices.

**Why we chose this.** Forced by the Postgres version. Neon runs 15.

**Alternatives.** Upgrade to Postgres 17; UUIDv7 generated in the application; composite
natural keys.

**Tradeoffs.** Sequences and IDENTITY differ in permissions and in how they behave with
`OVERRIDING SYSTEM VALUE`, but neither matters here. UUIDv7 would remove the sequence entirely
and be a reasonable choice for a distributed writer — but it is 16 bytes instead of 8 in every
index, and there is exactly one writer.

**Follow-ups.** "Would you use UUIDs?" · "What breaks on upgrade?" · "Any downside to
sequences?"

**What interviewers expect.** Recognition that this is a version-specific workaround with an
expiry date, not a design principle. If Neon upgrades to 17 the constraint disappears — and
there would be no reason to change anything, because the semantics already match.

---

### Q47. What is the DEFAULT partition for?

**Ideal answer.** It is a backstop. On a range-partitioned table, an insert whose partition key
falls outside every defined range **fails** — Postgres raises "no partition of relation found
for row." For a submissions table that would mean a student's work is rejected because
maintenance did not run. The DEFAULT partition catches those rows so the insert always
succeeds, and `ensure_submission_partitions` later relocates strays into the correct monthly
partition once it exists.

**Why we chose this.** An insert must never fail for lack of a partition. Availability of the
core write path beats partition purity.

**Alternatives.** No default, and rely on maintenance never lapsing; create partitions
far into the future; automate creation on insert via a trigger.

**Tradeoffs.** A DEFAULT partition can accumulate rows silently if maintenance stops, and it
also constrains adding new partitions — Postgres must scan the default to verify no conflicting
rows exist. The relocation step in the maintenance command is what keeps it near-empty.

**Follow-ups.** "What if the default fills up?" · "Is the command idempotent?" · "What if it
does not run?"

**What interviewers expect.** The self-healing property: the command is idempotent and
relocates strays, so recovery after downtime is automatic rather than manual. It runs in the
Render start chain, so every deploy repairs the horizon.

---

### Q48. Walk me through your index catalog.

**Ideal answer.** Five deliberate indexes, all declared on models and created by migration.
On `CodeSubmission`: `(user, -submitted_at)` for the submission history feed,
`(user, question, -submitted_at)` for "has this user solved this problem", and `(user, status)`
for accepted-count aggregates. On `Question`: `(topic, base_difficulty)`, which serves the
recommender's "problems in this topic near this Elo" query. On `RecommendationLog`:
`(user, problem_id, -created_at)`, which serves the flywheel lookup that finds the most recent
un-labelled recommendation to attach an outcome to.

**Why we chose this.** Each index maps to a query shape that actually runs in a hot path. The
architecture document calls this an "authoritative catalog" — new tables index on their query
shape at creation time rather than accumulating indexes reactively.

**Alternatives.** Index everything; index nothing and add reactively from slow-query logs;
covering indexes with INCLUDE.

**Tradeoffs.** Every index is write amplification and storage. On the submissions table —
the highest-write table — three indexes is already a real cost per insert, and I would not add
a fourth without a measured query to justify it.

**Follow-ups.** "Why descending on the timestamp?" · "Why no index on `question` alone?" ·
"How do you know these are used?"

**What interviewers expect.** The descending detail: `-submitted_at` matches `ORDER BY
submitted_at DESC`, so Postgres reads the index forward instead of backward. It matters less
than it used to, but it signals you thought about access direction rather than copying a
pattern.

---

### Q49. Why is there no single-column index on `user_id` in `CodeSubmission`?

**Ideal answer.** Because it would be redundant. `user_id` is the **leading column** of both
`subm_user_ts_idx` and `subm_user_status_idx`, and a B-tree index on `(a, b)` serves queries
filtering on `a` alone. Adding a standalone `user_id` index would duplicate that capability
while adding write cost and storage for nothing. Django creates FK indexes automatically, so
this was a deliberate omission recorded in the architecture's amendment log.

**Why we chose this.** Leading-column coverage is one of the highest-value index facts, and
most schemas violate it — you routinely see `(a)` and `(a, b)` side by side.

**Alternatives.** Keep the automatic FK index; use `db_index=False` explicitly.

**Tradeoffs.** The single-column `question_id` index **was** retained, because no composite in
the catalog leads with `question_id` — `subm_user_q_ts_idx` leads with `user`. So "all
submissions for this question, across users" has no other index to use. Same reasoning, opposite
conclusion.

**Follow-ups.** "So why keep the `question` index?" · "What is a leading column?" · "Would a
composite on `(question, user)` be better?"

**What interviewers expect.** This is a discriminating question and the asymmetry is the good
answer — dropping one FK index and keeping the other, for the same reason applied to different
catalog coverage. That is the difference between a rule of thumb and understanding.

---

### Q50. How do you prevent cycles in the curriculum graph?

**Ideal answer.** `TopicPrerequisite.clean()` builds a NetworkX `DiGraph` from all existing
edges except the one being saved, adds the proposed edge, and rejects the save if the result is
not a DAG. It also rejects self-prerequisites explicitly. Crucially, `save()` calls `clean()` —
Django does *not* call `clean()` automatically on save, only on ModelForm validation. Without
that override the constraint would exist and never run.

**Why we chose this.** A cycle in the prerequisite graph is unrecoverable at read time: the
`HierarchicalEngine` walks the DAG to find the next unlocked topic, and a cycle makes that walk
non-terminating or arbitrary. It has to be impossible to create, not detected later.

**Alternatives.** A database CHECK constraint (cannot express reachability); a recursive CTE
trigger; validate in the admin form only; a nightly integrity job.

**Tradeoffs.** Doing it in Python means loading every edge on every write — fine for a
curriculum with tens of topics, wrong for thousands. A recursive-CTE trigger would be
database-authoritative and immune to bulk operations that bypass `save()`.

**Follow-ups.** "What about `bulk_create`?" · "Why not a database constraint?" · "How
expensive is that check?"

**What interviewers expect.** The honest hole: `bulk_create`, `update()`, and raw SQL all
bypass `save()`, so the invariant is enforced only on the ORM path. Naming that unprompted is
the right move — and the note that this same class of gap is why the DAG cache invalidation
uses *signals* rather than an overridden `delete()`, so that queryset deletes are covered too.

---

### Q51. How does the DAG cache get invalidated?

**Ideal answer.** `post_save` and `post_delete` signal receivers on `TopicPrerequisite` call
`invalidate_dag_cache(subject)`. Signals rather than an overridden `delete()` specifically
because queryset-level deletes — like the one in the `seed_dsa_dag` command — do not call the
model's `delete()`. The receiver also defends against the cascade case: during a cascade the
topic may already be gone, so resolving the subject name is wrapped and falls back to
invalidating everything.

**Why we chose this.** A stale curriculum graph is a silent correctness bug — the
`HierarchicalEngine` would recommend from an outdated structure for up to the cache lifetime,
and nothing would report it.

**Alternatives.** Short TTL and accept staleness; explicit invalidation at every call site;
version the cache key on a curriculum revision number.

**Tradeoffs.** Signals are implicit — a reader of `seed_dsa_dag` sees no cache logic at all and
would not know invalidation happens. That is the standard signals criticism and it is fair. The
counter is that explicit invalidation at call sites is exactly what gets forgotten when a new
call site appears.

**Follow-ups.** "Why not just a short TTL?" · "What is the downside of signals?" · "What if the
cache is down?"

**What interviewers expect.** The queryset-delete reasoning, because it explains *why* signals
were chosen over the more obvious override. And ideally the version-key alternative: bumping a
`curriculum_version` integer and including it in the cache key makes invalidation atomic and
free, which is arguably better than deletion.

---

### Q52. What is `unique_together` doing in your schema?

**Ideal answer.** Five places, each preventing a specific duplicate-state bug.
`(user, topic)` on `UserTopicMastery` — one mastery row per user per topic, which is what makes
`get_or_create` safe. `(topic, prerequisite)` on the DAG edge — no duplicate edges.
`(sender, receiver)` on `Connection` — one friend relationship per direction.
`(user, badge)` on `UserBadge` — a badge is awarded once. `(user, section_name)` on the activity
model. Each is a database-level constraint, so a race that slips past application logic still
fails at the write.

**Why we chose this.** `get_or_create` is not atomic — two concurrent calls can both miss the
SELECT and both INSERT. The unique constraint is what turns that race into an
`IntegrityError` instead of a duplicate row.

**Alternatives.** Application-level checking only; `select_for_update` on a parent row;
`get_or_create` with retry.

**Tradeoffs.** Constraints give correct behaviour and unfriendly errors. In SparkLM the
`get_or_create` calls that matter — profile and mastery — happen *inside* a transaction holding
the profile lock, so the race is already precluded; the constraint is defence in depth.

**Follow-ups.** "Is `get_or_create` atomic?" · "What happens on the race?" · "Why is
`Connection` directional?"

**What interviewers expect.** The `get_or_create` race is a classic and you should be able to
state it precisely: SELECT-then-INSERT with no lock between them. Also the `Connection` nuance
— it is directional in storage but symmetric in meaning, so every friend query needs an `OR`
across both columns, and forgetting that silently returns half the friend list.

---

### Q53. Why is `CodeSubmission.question` nullable with CASCADE?

**Ideal answer.** `on_delete=CASCADE` means deleting a question deletes its submissions, and
`null=True` allows submissions whose question reference is absent. That combination is a
compromise from the content pipeline: questions get regenerated and replaced during reseeding,
and I did not want submission history to be the thing blocking a content repair. It has a real
cost, which I track as known debt — cascade deletes leave denormalised counters on
`UserCodingProfile` stale, because `total_submissions` and `successful_submissions` are
incremented in application code and nothing decrements them when rows vanish.

**Why we chose this.** Content operations needed to be able to delete bad questions.

**Alternatives.** `PROTECT` and force explicit cleanup; `SET_NULL`; soft deletes with an
`is_active` flag.

**Tradeoffs.** Soft deletes are probably the right answer here and I would change it: keep the
row, mark it retired, exclude it from `_servable_questions()`. That preserves history and
counters and costs one boolean. `PROTECT` would prevent counter drift but makes content repair
painful.

**Follow-ups.** "What is counter drift?" · "How would you fix it?" · "Why not recompute?"

**What interviewers expect.** Volunteering the drift as a known defect, and having a fix
ready: either recompute counters from `CodeSubmission` (they are derivable, so denormalisation
is an optimisation not a source of truth) or move to soft deletes. The stronger point is that a
denormalised counter is a *cache*, and every cache needs an invalidation story — this one does
not have one.

---

### Q54. How is per-question difficulty stored, and is that the right design?

**Ideal answer.** As `Question.base_difficulty`, a `FloatField(default=1200.0)` written only by
the seed commands. It is a **static author-assigned prior that never updates from outcomes** —
the Elo is one-sided, so a submission moves the user's rating and nothing else. It is not the
right design, and I can say precisely why: author-assigned difficulty is systematically wrong,
because authors know the solution and cannot estimate how hard it is to find. A problem
mislabelled "easy" that everyone fails stays labelled easy forever, and Elo-band selection
inherits the error.

**Why we chose this.** It was not a choice so much as an unfinished one. Two-sided Elo with
`rating`, `rating_deviation` and `attempt_count` is specified in `ARCHITECTURE_V2.md` §4.2, is
roadmap M10, and is Phase A of the Milestone 4 plan.

**Alternatives.** Two-sided Elo (planned); empirical pass rate; IRT difficulty parameters
fitted offline; expert review.

**Tradeoffs.** Raw pass rate is the tempting shortcut and it is **confounded** — a problem only
strong users attempt looks easy. Elo accounts for who attempted, because losing to a weak
player costs more than losing to a strong one. That is the argument for the planned design over
the obvious one.

**Follow-ups.** "Why has it not been done?" · "What would `rating_deviation` add?" · "How is
this different from IRT?"

**What interviewers expect.** Two things. The confounding argument, because it is what
separates Elo from a naive success-rate metric. And the architectural risk that makes this
non-trivial rather than a schema change: updating a question's rating inline introduces a lock
on a row **shared across all users**, where every existing lock is per-user. Every submission
to the deterministic first problem would contend on one row. The mitigation is deferred batch
aggregation, which keeps the lock-ordering contract intact — and knowing that is why the change
is Phase A rather than an afternoon.

---

### Q55. Why does `Document` have a 512-dimension vector column?

**Ideal answer.** `Document.feature_vector` is a pgvector `VectorField(dimensions=512)`, used
for similarity search over uploaded study materials. Storing embeddings in Postgres rather than
a dedicated vector database means one datastore, one backup, one connection pool, and the
ability to filter by ownership and metadata in the same query as the similarity search — which
matters because these documents are user-scoped and a vector store would need the ACL replicated
into it.

**Why we chose this.** Neon ships pgvector on the free tier, and the corpus is small. Adding
Pinecone or Weaviate would add a service, a bill, and a consistency problem for a workload that
fits comfortably in Postgres.

**Alternatives.** Pinecone, Weaviate, Qdrant; FAISS in-process; Elasticsearch dense vectors.

**Tradeoffs.** pgvector's ANN indexes (IVFFlat, HNSW) are good but not best-in-class at very
large scale, and building them costs memory — a real constraint on 512 MB. Below roughly a
million vectors, the operational simplicity wins decisively.

**Follow-ups.** "What index are you using?" · "Exact or approximate search?" · "When would you
move to a vector DB?"

**What interviewers expect.** Honesty about the index. Be ready to say whether an ANN index
exists or whether it is doing exact search over a small corpus — exact search is perfectly
correct at this scale and pretending to HNSW you have not built is the wrong move. Also the
filtering argument, which is the genuinely strong reason: combining `WHERE user_id = ...` with
vector similarity in one query is awkward across two systems.

---

### Q56. How do you handle migrations?

**Ideal answer.** Thirty-three migrations, run in the Render start chain before Daphne
launches. That is safe here specifically because there is exactly one instance — with two, a
rolling deploy would have old and new code against a mid-migration schema, which is the
classic way to take a service down during a "safe" deploy. The architecture requires
backward-compatible migrations for one release cycle, so the moment a second instance exists,
migrations move to a pre-deploy hook and every schema change becomes expand-then-contract.

**Why we chose this.** Free tier has no pre-deploy hook, and one instance makes the simple
approach correct.

**Alternatives.** Pre-deploy hooks; manual migration gates; a migration job in CI.

**Tradeoffs.** Migrations in the start chain mean a failed migration prevents boot — which is
arguably right, since running new code against an old schema is worse. But it also means a slow
migration delays every restart, including a restart triggered by an unrelated crash.

**Follow-ups.** "What is expand-then-contract?" · "What breaks with two instances?" · "How do
you roll back a migration?"

**What interviewers expect.** The expand-contract pattern named and explained: add the new
column nullable, deploy code writing both, backfill, deploy code reading new, then drop the
old. And the honest note that SparkLM has never needed it because it has never had two
instances — do not claim a practice you have not exercised.

---

### Q57. How would you archive old submission data?

**Ideal answer.** The architecture calls for `code` bodies moving to object storage after 90
days, and partitioning is what makes it tractable. The plan is: detach the target monthly
partition, export it, null out or externalise the `code` column, and either reattach a slimmed
partition or drop it entirely with the payload now in S3-compatible storage. Because it is a
partition-level operation, it does not lock the hot table or generate a mass-delete's worth of
WAL and bloat.

**Why we chose this.** `code` is the largest column on the largest table and the least
frequently read — nobody reviews a six-month-old wrong answer, but everyone's dashboard
aggregates over the whole history.

**Alternatives.** `DELETE` old rows in batches; a separate archive table; compress the column;
keep everything.

**Tradeoffs.** Batched deletes work but generate bloat and require VACUUM. Detaching a
partition is nearly instantaneous by comparison. The cost is that the archived code is now in a
second system with its own retrieval path.

**Follow-ups.** "Why not just delete?" · "What about the stats — do they survive?" · "Is this
implemented?"

**What interviewers expect.** That you say plainly it is **designed but not implemented** —
the partitioning is in place and the archival job is not. Claiming an unbuilt feature is the
fastest way to lose credibility, and "the expensive prerequisite is done, the job is not" is a
perfectly respectable state.

---

### Q58. What would you change about the schema if you started again?

**Ideal answer.** Three things. First, soft deletes on `Question` instead of CASCADE, which
removes the counter-drift problem entirely. Second, treat the denormalised counters on
`UserCodingProfile` as a derived cache with an explicit rebuild command, rather than as
authoritative values maintained by hand in application code. Third, I would not have added
per-topic `elo_rating` to the mastery model — it is written nowhere, and a field that exists
but is never updated is worse than an absent one, because code gets written against it. That
one actually caused a bug: a routing gate checked per-topic Elo ≥ 1300, a condition that could
never be satisfied, so the DAG recommended the root topic forever.

**Why we chose this.** Reflection.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "How did you find the Elo bug?" · "What is a derived cache?" · "Anything you
would keep exactly as-is?"

**What interviewers expect.** The vestigial-field story is the one to lead with, because the
failure mode is subtle and general: **an unused field looks like a usable field.** Someone
reasonably assumed it was maintained, wrote a gate against it, and the gate silently never
opened. Also say what you would keep — partitioning from day one, and the index catalog as an
explicit governed artifact rather than an accretion.

---

## Section E — Query Performance & Connection Management (Q59–Q70)

---

### Q59. How do you find slow queries?

**Ideal answer.** Three ways, in increasing cost. `CaptureQueriesContext` in tests gives exact
query counts per endpoint — that is how I know login is 1 query, dashboard bootstrap is 7, and
`/code/next/` is 16. Locally, `django-debug-toolbar` shows the SQL and timings. In production I
have no query-level instrumentation, so I infer: I measured endpoint latency against known
query counts and regressed, which gave a marginal cost of about 33 ms per additional query.
That number then explains most of the optimisation work in the project.

**Why we chose this.** The regression approach was forced by having no APM, but it turned out
to be genuinely useful because it produces a *planning* number — I can estimate the cost of a
new endpoint from its query count before writing it.

**Alternatives.** `pg_stat_statements`; `auto_explain`; APM tracing; slow-query logging.

**Tradeoffs.** `pg_stat_statements` is the right tool and Neon supports it; I did not wire it
up, which is a gap. Regression from endpoint latency is coarse and assumes queries are roughly
uniform, which they are not.

**Follow-ups.** "How did you get 33 ms?" · "Why not `pg_stat_statements`?" · "Is that number
still valid?"

**What interviewers expect.** The derivation, because it is a nice piece of reasoning:
`/dashboard/bootstrap/` at 7 queries took 937 ms server-side and `/code/next/` at 16 took
1231 ms, so the slope is 294 ms over 9 queries ≈ 33 ms each. Then flag the assumption — those
are two different endpoints doing different work, so the slope conflates query cost with
everything else that differs. It is an estimate, and you should call it one.

---

### Q60. What is the difference between `select_related` and `prefetch_related`?

**Ideal answer.** `select_related` does a SQL JOIN and populates related objects in one query —
it works for forward foreign keys and one-to-ones, where the related row is single-valued.
`prefetch_related` runs a second query and joins in Python — necessary for reverse foreign keys
and many-to-many, where a JOIN would multiply rows. In SparkLM the badge display uses
`select_related("badge")` because `UserBadge → Badge` is a forward FK, while group members
would need `prefetch_related` because it is many-to-many.

**Why we chose this.** Matching the tool to the cardinality is the whole decision.

**Alternatives.** Manual queries with `in_bulk`; raw SQL; `annotate` when you only need an
aggregate.

**Tradeoffs.** `select_related` on a wide table pulls every column of the joined row into every
result row. `prefetch_related` costs a round-trip, which on a cross-region database at ~33 ms
is not free. And often the right answer is neither: the dashboard needed a member *count*, so
`annotate(Count(...))` beat both.

**Follow-ups.** "What if you have both FK and M2M?" · "Can you `prefetch_related` a
`select_related`?" · "When is neither right?"

**What interviewers expect.** The "neither" case, because it is the one people miss. Fetching
related rows to compute `len()` is the most common N+1 fix that is still wrong — you have gone
from N+1 queries to 1 query that materialises data you throw away.

---

### Q61. Explain your connection pooling setup.

**Ideal answer.** psycopg3's native pool via Django's `OPTIONS['pool']`, with `min_size=2`,
`max_size=10`, `timeout=10`, `max_idle=300`, and `max_lifetime=1800`, plus
`CONN_MAX_AGE=0` and `CONN_HEALTH_CHECKS=True`. It replaced `CONN_MAX_AGE=60`, which never
worked under Daphne because each request runs on a fresh thread and `django.db.connections` is
thread-critical. The pool works because psycopg_pool lives in
`DatabaseWrapper._connection_pools`, a **class** attribute shared across all per-thread
wrappers, so it outlives the threads that thread-local connections cannot.

**Why we chose this.** Measured: 20 requests were opening 21 TCP sockets to Neon — one fresh
TCP + TLS + Postgres handshake per request, worth about 7.2 network round-trips. After pooling,
25 requests reuse 3 sockets. `/healthz` p50 went 699 ms to 391 ms; the database stage alone
went 421 ms to 109 ms.

**Alternatives.** External pooling only (Neon's PgBouncer, which I also go through);
`CONN_MAX_AGE` with a threading model that supports it; pgbouncer sidecar.

**Tradeoffs.** Pooling and persistent connections are mutually exclusive — Django raises
`ImproperlyConfigured` if `CONN_MAX_AGE` is non-zero with a pool. And it introduced a new
failure mode: demand above `max_size` waits, and raises `PoolTimeout` if the wait runs out,
turning overload into 500s where it previously just queued.

**Follow-ups.** "You already use Neon's pooler — why a second pool?" · "How did you size it?" ·
"What is the new failure mode?"

**What interviewers expect.** The two-pool question is the sharp one and you should have the
answer ready — see Q62.

---

### Q62. You already connect through Neon's PgBouncer. Why add a client-side pool?

**Ideal answer.** They solve different problems at different layers. PgBouncer multiplexes many
*client connections* onto fewer *server backends*, protecting Postgres from connection
exhaustion. It does nothing about the cost of my application opening a fresh TCP and TLS
connection *to PgBouncer* on every request — that handshake happens regardless of what is on
the other side. The client-side pool eliminates that. The proof is that `pg_backend_pid()` was
identical across arms in my A/B test — PgBouncer was already multiplexing correctly — while the
TCP socket count from the Daphne process dropped from 21 to 3.

**Why we chose this.** The measurement forced the distinction. I initially expected backend PID
to show the difference, and it could not.

**Alternatives.** Client pool only, connecting to the direct endpoint; PgBouncer only.

**Tradeoffs.** Two pooling layers is more moving parts and two places to misconfigure sizing.
The client pool must stay well under whatever PgBouncer allows, which it does at 10.

**Follow-ups.** "So what does PgBouncer buy you?" · "How did you measure socket count?" ·
"Could you drop one?"

**What interviewers expect.** The `pg_backend_pid()` anecdote, because it demonstrates a real
methodological correction: I chose a metric, discovered it could not distinguish the arms, and
switched to counting ephemeral local ports on TCP connections to port 5432 from the Daphne
process. Choosing the right instrument is most of measurement.

---

### Q63. Why is `CONN_HEALTH_CHECKS` mandatory rather than optional here?

**Ideal answer.** Because Neon's pooler drops idle connections and free-tier compute
auto-suspends after roughly five minutes, so a pooled connection can be dead when it is handed
out. With the setting off I observed the failure twice in testing — the log shows
`WARNING pool discarding closed connection: <Connection [BAD]>` immediately followed by
`ERROR Service Unavailable: /healthz`. The pool discards the corpse only *after* the request has
already failed. With it on, Django wires psycopg_pool's checkout validation and the dead
connection is replaced before the query runs. The check costs one round-trip; the handshake it
replaces costs about 7.2.

**Why we chose this.** Correctness over latency, with a favourable ratio.

**Alternatives.** No validation with application-level retry; tune `max_idle` below the
provider's timeout; periodic keepalive queries.

**Tradeoffs.** I do both — `max_idle=300` recycles idle connections before Neon's suspend
window, which reduces how often the check finds a corpse, but cannot remove the need for it
because suspension can happen at any moment.

**Follow-ups.** "Why not retry instead?" · "What does the check actually run?" · "What is
`max_lifetime` for?"

**What interviewers expect.** Why retry is not equivalent: by the time you would retry, the
request has already failed, so you are retrying at the application layer with all the
idempotency questions that raises. Validating on checkout keeps the failure invisible to the
request.

---

### Q64. How did you size the pool at 10?

**Ideal answer.** Honestly, judgement rather than measurement, and I flagged it as such in my
own review. The reasoning: queries are short — about 33 ms — so ten connections can serve
roughly 300 queries per second, which is far more than a single serialised worker can generate
at under 3 requests per second. It is sized for burst arrival, not sustained load. I did
verify it does not break under oversubscription: 25 concurrent queries against a `max_size=10`
pool all succeed, no `PoolTimeout`, and the pool never exceeds its maximum.

**Why we chose this.** Bounded generously enough that queueing is rare, small enough that it
cannot exhaust Neon's connection budget.

**Alternatives.** Size it to measured concurrency; make it configurable per environment;
`max_size` equal to worker count.

**Tradeoffs.** Too small means `PoolTimeout` under burst — a 500 where there used to be
slowness. Too large means idle connections against a provider quota. I would rather over-provision
here, because the failure mode of too-small is user-visible errors.

**Follow-ups.** "What happens at 11 concurrent?" · "How do you know it does not time out?" ·
"Would you make it configurable?"

**What interviewers expect.** The distinction between what you measured and what you chose.
`max_size=10` is a judgement call; the *oversubscription behaviour* is tested. Saying "these
five numbers are config, three are measured, two are taste" is far more credible than
retrofitting justification onto all of them.

---

### Q65. What is the marginal cost of a query in your system and why does it matter?

**Ideal answer.** About 33 ms. It matters because it reframes optimisation: on a normal
deployment you optimise queries, but here the *request* is far more expensive than the query.
An additional request costs the whole pipeline — middleware, JWT verification, a user lookup, a
throttle read, connection checkout — plus queueing behind everything else on a serialised
worker. That is why collapsing five dashboard requests into one beat any individual query
optimisation, and why endpoint count matters more than query count.

**Why we chose this.** It sets the optimisation priority order correctly.

**Alternatives.** N/A — it is a measurement.

**Tradeoffs.** N/A.

**Follow-ups.** "So query optimisation does not matter?" · "What about `/code/next/` at 16
queries?" · "Where is the crossover?"

**What interviewers expect.** Nuance rather than a slogan. Query count still matters — 16
queries on `/code/next/` is roughly 530 ms of pure database round-trips and is the obvious
target for that endpoint. The point is ordering: fix request count first, then query count,
then query shape. Most people do it in the reverse order.

---

### Q66. Your database is in Ohio and your app is in Virginia. Why?

**Ideal answer.** Because Neon's free tier and Render's free tier did not offer an overlapping
region I could use, and I chose persistence over co-location — Render's free Postgres expires
after 90 days, Neon's does not, and Neon ships pgvector which I need. `render.yaml` actually
carries a comment claiming they are on the same coast, which is true only loosely; they are
different AWS regions and the round-trip is real. It costs tens of milliseconds on every query,
which is exactly the 33 ms marginal cost.

**Why we chose this.** Persistence and pgvector beat latency for a project that must survive
past 90 days.

**Alternatives.** Render Postgres and accept expiry; Supabase; a paid Neon region matching
Render; move the app to Ohio.

**Tradeoffs.** Moving the app to match the database would be the cheapest latency win available
— it removes a cross-region hop from *every* query with no code change. That is a genuinely
good answer if asked what you would do next.

**Follow-ups.** "Could you just move the app?" · "How much would that save?" · "Why not
Supabase?"

**What interviewers expect.** That you have quantified it rather than hand-waving, and that you
correct your own documentation's over-claim. The comment in `render.yaml` says "same coast as
the Neon database"; the measurement says different regions. Catching your own stale comment is
a good look.

---

### Q67. What is a transaction isolation level and which do you use?

**Ideal answer.** Postgres default, READ COMMITTED — each statement sees data committed before
that statement began. I rely on explicit row locks via `select_for_update` rather than a
stricter isolation level. Under READ COMMITTED, a read-then-write is a race unless something
serialises it, which is precisely why the Elo farming guard sits inside the profile row lock.

**Why we chose this.** READ COMMITTED plus targeted pessimistic locking is predictable and does
not produce serialisation failures that need retry logic.

**Alternatives.** REPEATABLE READ; SERIALIZABLE; optimistic concurrency with a version column.

**Tradeoffs.** SERIALIZABLE would let me drop explicit locks and get correctness automatically —
but it raises serialisation failures under contention, and every write path would need a retry
wrapper. Explicit locks make the contention points visible in the code, which I prefer for a
codebase where the lock ordering is a documented contract.

**Follow-ups.** "What anomalies does READ COMMITTED allow?" · "Would SERIALIZABLE be simpler?"
· "What is a non-repeatable read?"

**What interviewers expect.** Naming the anomalies READ COMMITTED permits — non-repeatable
reads and phantoms — and mapping them onto your code: the farming guard's read-then-act is
exactly a non-repeatable-read hazard, handled with a lock rather than an isolation level.

---

### Q68. How do you test database behaviour?

**Ideal answer.** pytest-django against a real Postgres, not SQLite — the schema uses
partitioning, pgvector, and Postgres-specific index features that SQLite cannot represent, so
testing against it would test a different database. Concurrency tests use
`@pytest.mark.django_db(transaction=True)` because the default wraps each test in a transaction
that would never commit, making row locks and cross-thread visibility meaningless. Query counts
are asserted with `CaptureQueriesContext`.

**Why we chose this.** Testing against a different engine than production is a well-known way
to ship bugs that pass CI.

**Alternatives.** SQLite for speed; mocked ORM; a shared test database.

**Tradeoffs.** Real Postgres is slower — the suite runs in about 41 seconds. Worth it. Notably,
it got *faster* after connection pooling, from 107 seconds to 41, because tests were paying the
same per-connection handshake the application was.

**Follow-ups.** "Why does `transaction=True` matter?" · "How long is your suite?" · "Would you
use factories?"

**What interviewers expect.** The `transaction=True` reasoning, because it is the specific
thing people get wrong when writing concurrency tests — the default fixture's outer transaction
makes another thread unable to see your writes, so the test either hangs or passes vacuously.
And the suite-speedup detail is a nice incidental proof that the pooling fix was real.

---

### Q69. What happens if the database goes down?

**Ideal answer.** `/healthz` returns 503 within one query timeout, which tells Render the
instance is unhealthy. Every data-touching endpoint fails — there is no read replica, no cache
fallback for primary data, and no degraded mode. The Redis-backed caches would keep serving the
curriculum DAG and the leaderboard, so some reads would technically work, but authentication
requires a user lookup so effectively the application is down. That is accepted: this is a
single-region, single-instance deployment and database availability is the floor.

**Why we chose this.** Building a degraded mode for a dependency with no redundancy is effort
spent on a scenario you cannot actually survive.

**Alternatives.** Read replica; cache-aside for user data; a static maintenance page.

**Tradeoffs.** A read replica would allow degraded read-only operation and costs money plus
replication-lag reasoning. Given the whole product is write-heavy — every submission mutates
learner state — read-only mode would be nearly useless.

**Follow-ups.** "Could you serve anything?" · "What about Neon's auto-suspend?" · "How would
you add a replica?"

**What interviewers expect.** Awareness that Neon free-tier compute auto-suspends, so "database
unreachable" has a *routine* variant — a cold Neon wake of a few seconds, which is why the
health check keeps it warm and why the connection health check exists. Distinguishing routine
unavailability from an outage is the interesting part.

---

### Q70. How would you scale the database?

**Ideal answer.** In order: first `pg_stat_statements` to find out what actually costs, because
right now I would be guessing. Then a read replica for dashboard and leaderboard aggregates,
which are read-heavy and tolerate lag. Then partition maintenance automation and the archival
job, so the hot table stays small. Only after that would sharding come up, and the natural key
is `user_id` since almost every query is user-scoped — but that is a long way off, and the
application is CPU-bound before it is database-bound.

**Why we chose this.** Ordering by evidence: measure, then relieve reads, then bound data size,
then partition horizontally.

**Alternatives.** Vertical scaling; caching more aggressively; CQRS with a read model.

**Tradeoffs.** Read replicas introduce lag, and a student submitting then immediately loading
their dashboard could see stale state — a bad experience in a system whose whole feedback loop
is "submit and see the result."

**Follow-ups.** "What would you shard on?" · "Read-your-own-writes?" · "Is the database even
your bottleneck?"

**What interviewers expect.** The last question answered honestly: **no, it is not.** The
bottleneck is the single serialised worker and 0.1 of a vCPU. Scaling the database first would
be optimising the wrong layer, and saying so demonstrates you can resist an interesting problem
in favour of the real one.

---

## Section F — Caching (Q71–Q78)

---

### Q71. What do you cache and why?

**Ideal answer.** Three things, each for a different reason. The curriculum DAG — a NetworkX
graph built from every topic and prerequisite edge — because rebuilding it per recommendation
request is pure waste and it changes almost never. The top-three leaderboard on a 60-second
TTL, because it is identical for every user and slightly stale is fine. And DRF throttle
counters, which are not really a cache at all but shared state that happens to live in the
cache API. I deliberately do *not* cache user-specific learner state, because it changes on
every submission and a stale mastery value would corrupt the adaptive loop.

**Why we chose this.** The rule I applied: cache things that are expensive, shared, and
tolerant of staleness. The DAG is all three; learner state is none of them.

**Alternatives.** Cache per-user recommendations; cache serialised API responses wholesale;
no caching.

**Tradeoffs.** Caching recommendations would be the biggest win by request count and the most
dangerous — the recommendation depends on state that changes with every submission, so a cached
recommendation is a stale one by definition.

**Follow-ups.** "Why 60 seconds?" · "Why not cache the recommendation?" · "What is not cached
that should be?"

**What interviewers expect.** The explicit criteria, and a candidate answer for what *should* be
cached and is not: the RAG pipeline re-extracts and re-chunks the source document on every
question with no persisted index. That is the most expensive uncached work in the system.

---

### Q72. Two endpoints share the leaderboard cache key. Is that a good idea?

**Ideal answer.** Yes, deliberately. `GamificationDashboardView` and `DashboardBootstrapView`
both write and read `gamification_leaderboard_top3`, so whichever runs first populates it and
serves the other. A user loading the dashboard and then the gamification panel pays the query
once instead of twice. The risk with shared keys is that two writers disagree about the shape
of the value, so the two code paths construct the identical structure and the bootstrap view's
docstring notes the sharing explicitly.

**Why we chose this.** The leaderboard is global — the same top three for everyone — so
per-endpoint keys would be caching identical data twice.

**Alternatives.** Separate keys; a shared function both call; a cached property on a service.

**Tradeoffs.** A shared helper function would be cleaner and guarantee shape consistency. The
duplication exists because the bootstrap view intentionally avoids importing across view
modules so it can be deleted later without touching them — a documented trade of DRY for
deletability.

**Follow-ups.** "What if the shapes diverge?" · "Why not extract a function?" · "How would you
catch a divergence?"

**What interviewers expect.** Acknowledging the fragility. Shape divergence would be caught by
nothing today — there is no test asserting both endpoints produce the same leaderboard
structure, and that is a reasonable test to add.

---

### Q73. What cache invalidation strategies do you use?

**Ideal answer.** Two, matched to the data. TTL for the leaderboard: 60 seconds, no explicit
invalidation, because being a minute stale is harmless and the write path (any submission
changing any rating) is far too hot to invalidate on. Event-based for the curriculum DAG:
`post_save` and `post_delete` signals on `TopicPrerequisite` call `invalidate_dag_cache`,
because curriculum edits are rare and staleness there is a correctness problem, not a cosmetic
one.

**Why we chose this.** Write frequency versus staleness cost. High-write plus low-staleness-cost
gets a TTL; low-write plus high-staleness-cost gets explicit invalidation.

**Alternatives.** Write-through; versioned keys; cache-aside with explicit deletes everywhere.

**Tradeoffs.** Versioned keys — including a `curriculum_version` counter in the key — would be
strictly better than deletion: invalidation becomes an atomic integer bump, there is no
delete-then-read race, and old entries expire naturally. I would change to that.

**Follow-ups.** "What is the thundering herd?" · "Why not versioned keys?" · "What if
invalidation fails?"

**What interviewers expect.** The thundering-herd exposure: when the leaderboard key expires,
every concurrent request misses and all of them run the query. On a serialised worker that is
self-limiting, but the general fix — a lock or probabilistic early expiry — is worth naming.

---

### Q74. What is the difference between caching and memoisation here?

**Ideal answer.** Memoisation is per-process and per-lifetime; caching here is cross-process and
shared via Redis. That distinction is load-bearing in SparkLM because the throttle counters
*must* be shared — a per-process memo of request history would mean each worker enforcing its
own independent limit, so N workers would allow N times the intended rate. Today there is one
process so it would accidentally work, and it would break silently on the day a second is added.

**Why we chose this.** Anything that is a *control* rather than an *optimisation* must be
shared state.

**Alternatives.** `functools.lru_cache` for pure computations; per-process caches for immutable
data.

**Tradeoffs.** In-process is far faster — no serialisation, no network — and the Redis throttle
read costs about 50 ms per request. For genuinely immutable derived data, in-process is right.

**Follow-ups.** "Could the DAG be in-process?" · "What breaks with multiple workers?" · "Is
50 ms worth it?"

**What interviewers expect.** The multi-worker reasoning applied to each cached item: the DAG
*could* be per-process with a short TTL because a stale graph in one worker is survivable;
throttle counters absolutely cannot. Distinguishing per-item is better than a blanket answer.

---

### Q75. Tell me about a caching bug you have hit.

**Ideal answer.** The worst production incident in the project. The cache was configured and
appeared healthy, but it **accepted writes and returned nothing**. Django's cache API has no
failure signal for that — `cache.set()` succeeds, `cache.get()` returns `None`, which is
indistinguishable from a legitimate miss. Three things degraded in total silence: every DRF
throttle (because `cache.get(key, [])` returned `[]`, so no limit was ever reached), the
curriculum DAG cache, and the leaderboard. Nothing errored. The test suite stayed green, because
tests use LocMemCache, which works — an environment-only fault is structurally invisible to
them.

**Why we chose this.** The fix was `verify_cache_backend()`: a boot-time probe that writes a
sentinel, reads it back, and logs an actionable ERROR if the value does not round-trip. It never
raises, because degraded caching is survivable and refusing to boot is not.

**Alternatives.** Health-check endpoint verifying the cache; monitoring cache hit rate; fail
fast on startup.

**Tradeoffs.** A boot probe catches configuration faults but not a cache that dies at hour six.
Continuous monitoring would catch that and costs infrastructure I do not have.

**Follow-ups.** "How did you find it?" · "Why not fail fast?" · "What else could be silently
broken?"

**What interviewers expect.** The security framing, because that is what makes this more than a
performance story: **the credential-stuffing brake on `/token/` and the cap on the metered
Judge0 API were both completely inert**, and every test asserting they worked was passing. A
security control that fails open, silently, with green tests, is close to the worst possible
failure mode — and you found it.

---

### Q76. How do you test caching behaviour?

**Ideal answer.** With a fake backend that reproduces the failure, not a mock. I wrote a
`DummyCache` that accepts writes and returns nothing — exactly the production fault — and a
`RaisingCache` that throws. Eight tests assert the probe detects each condition, logs at the
right level, names the consequence in the message, and never raises. Separately, there is a
test pinning the *causal link*: with a working cache the throttle fires on the expected request,
and with the non-persisting cache it never fires at all. That second test is the one that makes
the incident non-repeatable.

**Why we chose this.** A test that mocks `cache.get` to return a value proves nothing about a
backend that lies.

**Alternatives.** Mock the cache API; integration test against real Redis; skip it.

**Tradeoffs.** Testing against real Redis in CI would be more faithful and needs a service
container. The fake-backend approach catches the specific class of fault I actually hit.

**Follow-ups.** "Why not test against real Redis?" · "What does the causal test assert?" ·
"Would this have caught the original bug?"

**What interviewers expect.** Honesty on the last one: **no.** The original bug was an
environment fault, and no unit test would have caught it — that is precisely why the response
was a *boot probe* rather than a test. The tests protect the probe; the probe protects
production. Being clear about which artifact defends against which failure class is the sign of
someone who has thought it through.

---

### Q77. What would you cache that you currently do not?

**Ideal answer.** The RAG chunk index, without question. Today `/api/ai/doubt/rag/` re-reads
the document from disk, re-extracts text, and re-splits it into 500-character chunks on **every
single question**, with no persisted index. `Document.feature_vector` exists for the
visual-search path but is not used here. Persisting chunks and embeddings once at upload would
turn the per-question cost into a vector lookup. On a serialised worker that is the single most
expensive uncached operation in the system.

**Why we chose this.** It was built for correctness first and never revisited.

**Alternatives.** Cache the extracted text only; cache chunks; cache chunks plus embeddings;
cache full answers by question hash.

**Tradeoffs.** Caching answers by question hash would be the biggest win and the most dangerous
— two students asking similar questions get identical answers, and the LLM's response may
depend on context the hash does not capture.

**Follow-ups.** "Where would you store the chunks?" · "How would you invalidate them?" ·
"Would you cache the LLM response?"

**What interviewers expect.** A concrete design: chunk and embed at upload time, store chunks in
a table with a foreign key to `StudyMaterial` and a pgvector column, invalidate on document
replacement. That is a small, well-scoped Milestone 4 item and having it specified shows you
have already thought past the current state.

---

### Q78. How do you decide TTL values?

**Ideal answer.** From how stale the data can be before it is wrong, not from how expensive it
is to compute. The leaderboard is 60 seconds because a leaderboard that lags a minute is
indistinguishable from one that does not, to a human. The DAG has no TTL at all — it is
invalidated on change — because a stale curriculum is a correctness bug rather than a cosmetic
one, and there is no duration of staleness that is acceptable.

**Why we chose this.** Staleness tolerance is a product property. Compute cost only tells you
whether caching is worth doing, not for how long.

**Alternatives.** Uniform TTL; TTL proportional to compute cost; adaptive TTL from hit rate.

**Tradeoffs.** The 60-second leaderboard bit me once during measurement — I ran a cache-behaviour
probe with a slow shell command between two calls, the entry expired in between, and I
misdiagnosed it as the cache not persisting. I shipped a fix for the wrong root cause before
catching it.

**Follow-ups.** "Have you ever picked a TTL wrong?" · "How would you tune it?" · "What about
cache stampede?"

**What interviewers expect.** That misdiagnosis story, because it is a good measurement lesson:
**my probe's own runtime exceeded the TTL I was testing.** The instrument perturbed the
measurement. Volunteering a case where you shipped a fix for the wrong cause — and then
corrected the record — is exactly the kind of honesty that reads as senior.

---

## Section G — Redis (Q79–Q84)

---

### Q79. What does Redis do in SparkLM?

**Ideal answer.** Three distinct jobs through one Upstash instance: the Django cache backend
(curriculum DAG, leaderboard), the DRF throttle store, and the Channels layer for WebSocket
group messaging. The throttle role is the one people underestimate — that is not caching, it is
shared security state, and it is the reason Redis is not optional in production.

**Why we chose this.** One managed instance covers all three, and none of them individually
justifies a service.

**Alternatives.** Separate instances per concern; the database for throttles; in-memory for
cache.

**Tradeoffs.** Sharing one instance means a Redis outage takes out caching, rate limiting, and
cross-process WebSockets simultaneously. The architecture actually specifies two logical Redis
instances — one for cache and channels, a second as a Celery broker in a later phase — so the
split is planned.

**Follow-ups.** "What breaks if Redis dies?" · "Why not use the database for throttles?" ·
"Why Upstash?"

**What interviewers expect.** The failure analysis: throttling fails **open**, which is a
security degradation, while caching fails to a slow-but-correct path. Those are very different
severities sharing one dependency, and noticing the asymmetry is the good answer.

---

### Q80. What happens when `REDIS_URL` is unset?

**Ideal answer.** Django falls back to `LocMemCache` and Channels to an in-memory layer. That is
correct for local development and catastrophic in production, because both fallbacks are
per-process. Throttle counters become per-process, so limits multiply by worker count. Channels
`group_send` stops crossing processes, so a chat message published by one worker is invisible to
sockets held by another. Today both are masked by there being exactly one process — which means
they are latent bugs waiting on a configuration change, not bugs you would ever see in testing.

**Why we chose this.** The fallback exists so `git clone` and `runserver` work with no
infrastructure. That is worth keeping.

**Alternatives.** Refuse to start without Redis in production; require it always; a file-backed
cache.

**Tradeoffs.** Refusing to start is tempting, and I chose the boot probe instead — log loudly,
keep serving. The probe covers the cache path; it does not currently verify the channel layer,
which is a gap.

**Follow-ups.** "Would you fail fast instead?" · "Does the probe catch the channel layer?" ·
"How would you test multi-process behaviour?"

**What interviewers expect.** The phrase "masked by a deployment constraint." A bug that is
currently invisible because of a coincidence of your topology, and will appear silently the day
someone scales to two instances, is a genuinely dangerous category — and being able to name
which of your bugs are in it is a strong signal.

---

### Q81. How does the Channels layer use Redis?

**Ideal answer.** `channels_redis.core.RedisChannelLayer` uses Redis as a pub/sub bus.
When `GroupChatConsumer` calls `group_send("chat_42", message)`, the message is published to
Redis and every process holding a socket in that group receives it and forwards it to its
clients. Group membership — the mapping from group name to channel names — also lives in Redis.
That is what makes horizontal scaling of WebSockets possible at all: without it, each process
only knows about its own sockets.

**Why we chose this.** It is the standard Channels production backend, and it is the same Redis
I already need for caching.

**Alternatives.** In-memory layer (single process only); a dedicated message broker; Postgres
LISTEN/NOTIFY.

**Tradeoffs.** Channels-over-Redis adds latency to every message and makes Redis a hard
dependency for chat. LISTEN/NOTIFY would avoid a second system but has payload size limits and
no consumer groups.

**Follow-ups.** "How does group membership work?" · "What if Redis is slow?" · "Does the
architecture use a second Redis?"

**What interviewers expect.** That you know the architecture specifies **Redis A** for cache,
throttles, and channels, and **Redis B** as a Celery broker in a later phase — separated so that
a flood of background jobs cannot evict cache entries or delay chat messages. Knowing the
planned split shows the current single instance is a stage, not an oversight.

---

### Q82. Is Redis single-threaded, and does that matter to you?

**Ideal answer.** Yes — command execution is single-threaded, which is what makes individual
commands atomic without locking. It matters to me in one specific way: DRF's throttle is a
non-atomic **read-modify-write** across multiple commands, so Redis being atomic per command
does not make the throttle atomic. Two truly simultaneous requests can both read the same
pre-increment history and both pass. It is a rate limiter, not a semaphore, and I document it as
such.

**Why we chose this.** Accepting the limitation rather than reimplementing DRF's throttle.

**Alternatives.** A Lua script for atomic check-and-increment; `INCR` with expiry as a sliding
window; a token-bucket implementation.

**Tradeoffs.** A Lua script executes atomically on the server and would close the gap in about
fifteen lines. I did not do it because on a serialised worker true simultaneity is rare, and the
limits are already set conservatively below capacity. On a multi-worker deployment I would fix
it.

**Follow-ups.** "How would you make it atomic?" · "Have you seen it slip?" · "What is a token
bucket?"

**What interviewers expect.** Precision about *where* the atomicity gap is. Many candidates
answer "Redis is single-threaded so it is atomic" and stop. The interesting answer is that
per-command atomicity does not compose into multi-command atomicity, and naming Lua or `INCR`
as the fix shows you know the remedy.

---

### Q83. How would you implement rate limiting from scratch in Redis?

**Ideal answer.** A sliding-window counter with a sorted set: `ZREMRANGEBYSCORE` to drop entries
older than the window, `ZCARD` to count what remains, `ZADD` the current timestamp if under the
limit, and `EXPIRE` on the key so it self-cleans. Wrapped in a Lua script it is atomic, which
fixes the read-modify-write gap in DRF's implementation. A cheaper approximation is a fixed
window — `INCR` a key named for the current minute with a TTL — which costs one command but
allows a burst of up to twice the limit across a window boundary.

**Why we chose this.** SparkLM uses DRF's built-in throttle, which is a sliding window over a
Python list stored in the cache — correct enough, and not atomic.

**Alternatives.** Token bucket for burst tolerance; leaky bucket for smoothing; GCRA.

**Tradeoffs.** Token bucket is nicer for real traffic because it allows short bursts while
bounding sustained rate — which suits a user clicking around a dashboard. Sliding-window
counters are simpler to reason about and to explain to a user via `Retry-After`.

**Follow-ups.** "What is the boundary problem?" · "Token bucket vs sliding window?" · "How do
you communicate the limit to clients?"

**What interviewers expect.** The fixed-window boundary problem stated concretely: with a limit
of 5 per minute, five requests at 11:59:59 and five at 12:00:01 is ten in two seconds, all
legal. That is the standard follow-up and having the example ready is worth more than the
terminology.

---

### Q84. Your throttling was completely inert in production. What happened?

**Ideal answer.** Two separate causes, and I initially diagnosed the wrong one. The symptom was
that no request ever got a 429. My first conclusion was that the cache was not persisting, and I
shipped a fix for that. It was wrong — my probe ran a slow shell command between two calls
against a 60-second TTL, so the entry had simply expired. The real cause was client
identification: DRF's `NUM_PROXIES=1` keys on the **last** `X-Forwarded-For` hop, and on Render
that is a rotating internal load balancer. Twelve sequential requests to `/token/` landed in
**three different buckets**, none of which reached the limit. The fix was a throttle class
keying on the first hop instead.

**Why we chose this.** The first hop is the real client. It is spoofable, which is a genuine
weakening — but the prior behaviour required *no* evasion effort at all, so a spoofable limit
strictly dominates an absent one.

**Alternatives.** Correct `NUM_PROXIES` for the topology (Render's hop count is not stable);
edge rate limiting at the CDN; authenticated-only limits.

**Tradeoffs.** Client-supplied XFF means an attacker rotating the header gets a fresh bucket.
That cost is accepted, documented, and **test-pinned** — there is a test asserting the evasion
works, so if someone later hardens identification the test fails and they update it deliberately.

**Follow-ups.** "Why is a spoofable limit acceptable?" · "How did you find the rotation?" ·
"You tested that the evasion works?"

**What interviewers expect.** Three things, in order. The misdiagnosis and the correction —
volunteer it. The evidence: twelve requests, three buckets, with the actual internal IPs
observed. And the test that asserts your own limitation, which usually surprises people: writing
a test that pins a *weakness* means the weakness cannot be silently removed or silently
worsened. That is an unusual and strong instinct.

---

## Part 2 Recap — Five More Stories

| # | Story | The one-line hook |
|---|---|---|
| 6 | **Two pools, one problem** | PgBouncer was already multiplexing perfectly — `pg_backend_pid()` was identical across both arms — and the handshake cost was entirely client-side. |
| 7 | **The partition tax** | Partitioning bought a data-lifecycle story and cost me IDENTITY columns, a simple PK, and the unique constraint I wanted for the farming guard. |
| 8 | **The dead index that never was** | Dropping the `user_id` FK index and keeping the `question_id` one — same rule, opposite conclusion, because of leading-column coverage. |
| 9 | **The TTL that ate my measurement** | My probe's own runtime exceeded the TTL it was testing, and I shipped a fix for the wrong root cause before catching it. |
| 10 | **Three buckets, no brake** | Rate limiting was inert because Render's load balancer rotates IPs — twelve requests, three buckets, zero 429s, all tests green. |

The theme in Part 1 was silent failure. The theme here is **the instrument being wrong**:
`pg_backend_pid()` could not see the difference, the TTL outlived nothing, `AsyncClient` reused
a thread, and the throttle keyed on an address that changed every request. In four of five
cases the *system* was legible and the *measurement* was not.

---

*End of Part 2 (Questions 43–84). Part 3 — Authentication, JWT & Security — follows.*
