# SparkLM Technical Interview Handbook
## Part 6 — LLMs, RAG, Prompt Engineering, Judge0 & System Design

**Questions 213–254 of 254 — final part**
**Companion:** Document 03 (AI Pipeline), Document 01 §6, §9, §15

---

## Section T — LLM Integration (Q213–Q224)

---

### Q213. How is the LLM integrated?

**Ideal answer.** One module, `groups/ai_services.py`, is the sole integration point for every
LLM and embedding call — question generation, test-case generation, starter stubs, tutoring,
quizzes, flashcards and RAG all route through it. Primary provider is Groq running
`llama-3.3-70b-versatile`; NVIDIA NIM with `meta/llama-3.1-70b-instruct` is a narrow fallback;
Gemini `text-embedding-004` handles embeddings. Centralising it means the retry policy,
validation rules and fallback logic exist once rather than being reimplemented per feature.

**Why we chose this.** LLM calls are the least reliable thing in the system, so the handling has
to be uniform. Scattered `client.chat.completions.create` calls would mean scattered failure
behaviour.

**Alternatives.** LangChain; a per-feature client; direct calls from views.

**Tradeoffs.** A single module becomes large and mixes concerns — generation, validation and
provider selection all live together. It would benefit from splitting into a provider layer and a
task layer, and it has not been.

**Follow-ups.** "Why not LangChain?" · "Why Groq?" · "How do you switch providers?"

**What interviewers expect.** A concrete reason for rejecting LangChain: the abstraction cost
exceeds the benefit for a handful of prompt shapes, and it obscures exactly the thing that needs
to be explicit here — what happens when a call fails. Choosing not to adopt a popular framework,
with a reason, reads better than adopting it by default.

---

### Q214. Why Groq specifically?

**Ideal answer.** Inference speed and a usable free tier. Groq's hardware serves Llama models at
very high tokens per second, which matters because these calls sit inside user-facing requests on
a backend where requests queue — a slow LLM call does not just make one request slow, it occupies
the single worker. `llama-3.3-70b-versatile` is capable enough for structured generation of
questions and test cases.

**Why we chose this.** Latency on a serialised worker is worth more than marginal model quality.

**Alternatives.** OpenAI; Anthropic; self-hosted; Together; Gemini for generation as well as
embeddings.

**Tradeoffs.** Free tiers come with **daily quotas**, which is precisely why the fallback exists.
And an open-weights Llama model is weaker than a frontier model at complex structured output, so
the validation and retry logic carries more load than it would with a stronger model.

**Follow-ups.** "What about quality?" · "Why not one provider for everything?" · "What is the
quota?"

**What interviewers expect.** The insight that provider choice interacts with the concurrency
model. On a parallel backend, a slow LLM call blocks one request; here it blocks the queue. That
is a systems-level reason for a model choice, which is a more interesting answer than benchmark
scores.

---

### Q215. Explain your fallback strategy.

**Ideal answer.** `_generate_json_with_fallback` calls Groq, and if it fails checks
`_is_daily_quota_error`. If that is false — a transient error, a timeout, a malformed response —
it **raises rather than falling back**. It only switches to NVIDIA NIM when the quota is
genuinely exhausted. The narrowness is the design: a broad fallback would mask transient faults
and double the latency of every failure, and on a serialised worker that is expensive.

**Why we chose this.** Fall back only on the condition that retrying cannot fix. Everything else
should surface.

**Alternatives.** Fall back on any error; retry with backoff then fall back; round-robin
providers.

**Tradeoffs.** Narrow fallback means a Groq outage that is not a quota error takes the feature
down even though a working alternative exists. That is deliberate — I would rather see the outage
than silently serve from a slower, less-tested path.

**Follow-ups.** "Why not fall back on any error?" · "What if Groq is just down?" · "Have you
tested the fallback?"

**What interviewers expect.** Pushback handled well. Most candidates treat fallbacks as
unambiguously good, so explaining that a *broad* fallback is harmful — it hides faults and doubles
failure latency — demonstrates thinking about failure taxonomy rather than pattern-matching to
"add resilience." Concede the outage case as a real cost.

---

### Q216. Why 150 seconds for the NIM timeout?

**Ideal answer.** Measurement. The first choice, `llama-3.3-70b-instruct` on NIM, was observed
**queueing for over two minutes** on shared capacity, so a 60-second timeout failed every time.
I switched the model to `llama-3.1-70b-instruct`, which had better availability, and widened the
timeout to 150 seconds to match observed behaviour rather than an assumption.

**Why we chose this.** The timeout describes what the dependency actually does, not what I wished
it did.

**Alternatives.** Keep 60 seconds and accept failures; async with polling; a different provider.

**Tradeoffs.** 150 seconds is a very long time to occupy a serialised worker — that single request
could block roughly 400 healthz requests' worth of capacity. It is tolerable only because the
fallback fires rarely, and it would be unacceptable on a hot path.

**Follow-ups.** "Is that not too long?" · "What blocks during that?" · "Should it be async?"

**What interviewers expect.** Recognising the timeout is defensible only *because* the path is
rare. If the fallback were common, 150 seconds synchronous would be an outage waiting to happen,
and the right design would be a background job. Knowing that a parameter's acceptability depends
on its frequency is the nuance.

---

### Q217. How do you handle non-deterministic LLM output?

**Ideal answer.** Never trust it. `_valid_test_cases` structurally validates generated test cases,
including a **string type-check** on `stdin` and `expected_output` — added because a model
returned integers once and broke downstream grading, which expects strings to strip and compare.
Placeholder text is filtered out. Validation happens **inside** the retry loop, returning
`(ai_data, last_error)`, so an invalid generation is retried rather than persisted.

**Why we chose this.** An LLM that returns prose instead of JSON should produce a retry, not a
crash and not a corrupt row.

**Alternatives.** JSON mode / structured output; function calling; Pydantic parsing with retries;
grammar-constrained decoding.

**Tradeoffs.** Provider-side JSON mode would guarantee syntactic validity and does nothing for
*semantic* validity — a well-formed JSON object with integer `stdin` still breaks grading. So
application-level validation is required regardless, which is why I did not lean on it.

**Follow-ups.** "Why not JSON mode?" · "What is a placeholder?" · "How many retries?"

**What interviewers expect.** The syntactic-versus-semantic distinction, and the integer bug as
the concrete example. It is a small detail that demonstrates the validation was written against
observed failures rather than imagined ones.

---

### Q218. Why is validation inside the retry loop?

**Ideal answer.** Because an invalid response is a *failure* and should be retried like one.
Originally validation happened after the loop, so a malformed generation exhausted no retries —
it just failed. Moving it inside, with the function returning `(ai_data, last_error)`, means the
loop can try again and the caller gets the last error for logging when all attempts fail.

**Why we chose this.** The distinction between "the call failed" and "the call succeeded and
returned garbage" is not useful to the caller — both mean no usable output.

**Alternatives.** Validate after; separate validation retries; accept and clean up later.

**Tradeoffs.** Retrying on validation failure costs latency and quota when the model is
persistently producing bad output for a particular prompt. A retry budget bounds that, but it
does not distinguish "unlucky sample" from "this prompt does not work."

**Follow-ups.** "How many retries?" · "What if it always fails?" · "Do you log the bad output?"

**What interviewers expect.** That `last_error` is returned rather than swallowed. A retry loop
that discards the reason for failure is nearly useless for debugging, and this one was
specifically restructured so the caller can log what actually went wrong.

---

### Q219. How do you validate generated code stubs?

**Ideal answer.** With a language-aware check. The original filter tested whether the generated
stub contained `Solution`, which works for Python and Java where the wrapper reflects over a
`Solution` class. It fails for C, which has no classes at all, so every valid C stub was rejected.
The fix is `(lang == "c" or "Solution" in code)` — an explicit exemption for the language whose
model differs.

**Why we chose this.** A universal check across languages with genuinely different structures is a
bug waiting to happen.

**Alternatives.** Per-language validators; compile the stub via Judge0 to verify; no validation.

**Tradeoffs.** Compiling every generated stub through Judge0 would be the strongest validation and
costs a metered API call per stub during generation of hundreds of questions. String checking is
cheap and shallow.

**Follow-ups.** "Why not compile it?" · "What about C++?" · "Is this the wrapper contract?"

**What interviewers expect.** Connecting this to the Milestone 2 template bug: I shipped a Java
template authored against the problem statement rather than the wrapper, never executed it, and
it compile-errored on the first problem every new user receives. **A template that has never been
executed is not a template, it is a guess** — and Judge0-verifying generated stubs is the version
of that lesson applied to content generation.

---

### Q220. How do you keep LLM costs and quota under control?

**Ideal answer.** Three mechanisms. Generation happens offline in management commands —
`reseed_questions` and `backfill_boilerplate` — rather than per user request, so content
generation is a batch cost paid once per question rather than repeatedly. Request-time LLM
endpoints are throttled: `/code/next/` at 30 per minute under the `recommend` scope, because it
can trigger test-case generation. And the quota fallback keeps the service functional when Groq's
daily allowance is exhausted.

**Why we chose this.** Moving generation offline is the structural fix; throttling is the
backstop.

**Alternatives.** Cache LLM responses; smaller models for simpler tasks; generate on demand only.

**Tradeoffs.** Offline generation means content quality is fixed at generation time and improving
prompts requires a regeneration pass over 1,100-plus questions, rate-limited by quota. There is a
`--retry-failed` mode precisely because these runs partially fail and need resuming.

**Follow-ups.** "Why throttle `/code/next/`?" · "How long does a reseed take?" · "Would you cache
responses?"

**What interviewers expect.** The operational reality of a large generation run: partial failure
is the normal case, so the command has to be resumable, idempotent, and re-apply its filters on
retry. Designing batch jobs for partial failure rather than success is the practical detail.

---

### Q221. Walk me through `reseed_questions`.

**Ideal answer.** It generates a full question — description, test cases and per-language starter
stubs — for questions that are placeholders or incomplete. Key design points: validation moved
*inside* the retry loop so a bad generation is retried; a string type-check on `stdin` and
`expected_output`; `--retry-failed` re-applies the placeholder filter so it does not reprocess
work already completed; and coverage logging for `BONUS_LANGUAGES` so I can see which languages
are being filled and which are not.

**Why we chose this.** It is the content pipeline, and the pipeline's failure modes are quota
exhaustion and malformed output — so it is built around resuming and validating rather than
around a clean run.

**Alternatives.** Manual authoring; a one-shot script; a hosted content service.

**Tradeoffs.** Generated content is uneven, which is why multi-language coverage is only about
35%. Manual authoring would be higher quality and does not scale to a question bank of this size.

**Follow-ups.** "Why is coverage only 35%?" · "What does `--retry-failed` do?" · "How do you
verify quality?"

**What interviewers expect.** That coverage gaps are handled honestly downstream: the frontend
**labels and disables** a language with no template rather than opening an empty editor. The
content pipeline being incomplete is acceptable; hiding that from the user is not.

---

### Q222. How do you test LLM integration?

**Ideal answer.** By injecting the boundary. External I/O is passed into services as callables
rather than imported, so tests supply stubs — the same pattern as the Judge0 runner. There is a
dedicated `test_ai_services_fallback.py` covering the fallback logic specifically: that a quota
error triggers NIM and a non-quota error does not. So the *decision logic* is tested offline
while the model output itself is not.

**Why we chose this.** Testing against a live LLM would be slow, non-deterministic, quota-consuming
and flaky — four independent reasons.

**Alternatives.** Recorded fixtures; a local small model; snapshot testing on outputs; skip.

**Tradeoffs.** Nothing tests that the prompts actually produce good questions. Prompt quality is
validated by inspecting output during reseed runs, which is manual and unsystematic.

**Follow-ups.** "How do you test prompt quality?" · "What about regressions in output?" · "Would
you use an LLM judge?"

**What interviewers expect.** Distinguishing testing the *integration* from testing the *model*.
The integration is deterministic and fully testable — retries, fallback conditions, validation
rules. The model is not, and the honest approach is a small golden set with an LLM-as-judge or
human review, which I do not have.

---

### Q223. What is the biggest risk in your LLM usage?

**Ideal answer.** Generated content reaching students unvalidated. A question with wrong test
cases teaches the wrong thing and, worse, marks correct solutions as failures — which destroys
trust in the grader. The mitigations are structural validation, the placeholder filter, and
`_servable_questions()` excluding anything with zero test cases. What is *not* mitigated is a
question with syntactically valid but semantically wrong test cases — expected output that is
simply incorrect.

**Why we chose this.** That failure is invisible to every automated check I have.

**Alternatives.** Run a reference solution against generated cases; human review; cross-validate
with a second model.

**Tradeoffs.** Executing a reference solution against the generated test cases via Judge0 would
catch it definitively and costs an API call per question. Given the damage a wrong expected-output
does, that is probably worth paying, and it is the strongest unimplemented improvement in the
content pipeline.

**Follow-ups.** "How would you catch it?" · "Has it happened?" · "What does the student see?"

**What interviewers expect.** The Judge0-verification proposal, because it closes the loop using
infrastructure that already exists. And the observation that this is the same lesson as the
untested Java template, one level up: **content that has never been executed is a guess.**

---

### Q224. Would you fine-tune a model?

**Ideal answer.** No, and I can say why specifically rather than generically. The tasks are
structured generation — question text, test cases, stubs — which prompting handles well, and
fine-tuning needs a labelled dataset I do not have. The place fine-tuning would genuinely help is
hint generation for the agentic coach, where a model tuned on good pedagogical hints for this
problem set would beat a general model. That needs hundreds of rated examples, and I would collect
them by logging coach hints alongside whether the student solved the problem afterwards.

**Why we chose this.** Prompting first; fine-tune only where you have data and a measurable
target.

**Alternatives.** LoRA on an open model; few-shot prompting; RAG over exemplars; a distilled small
model.

**Tradeoffs.** Fine-tuning adds a training pipeline, versioning, and evaluation to a project that
does not yet have model monitoring. Few-shot prompting with good exemplars captures most of the
benefit at none of the operational cost.

**Follow-ups.** "What data would you need?" · "LoRA or full?" · "How would you evaluate it?"

**What interviewers expect.** Identifying the *one* task where fine-tuning is justified and the
data-collection path to get there. "No, but here is exactly when yes, and here is how I would
gather the dataset" is far stronger than either a flat no or an enthusiastic untethered yes.

---

## Section U — RAG & Embeddings (Q225–Q234)

---

### Q225. Explain your RAG pipeline.

**Ideal answer.** A student asks a question about an uploaded study material.
`RAGDoubtView` loads the `StudyMaterial`, and if it is an image routes to a vision path. For
documents it extracts text, rejects anything under 50 characters as unextractable, splits with
`RecursiveCharacterTextSplitter` at 500 characters with 50 overlap, and passes the chunks to
`RAGService.answer_with_rag`, which returns an answer plus citations. The response includes a
`mode` field so the client knows whether it got a RAG answer or a vision answer.

**Why we chose this.** Two modalities behind one endpoint, dispatched on file extension, because
students upload both PDFs and photographs of notes.

**Alternatives.** Separate endpoints per modality; OCR images into the text path; reject images.

**Tradeoffs.** The `mode` field matters because vision answers have **no citations**, so the
client cannot assume a uniform response shape. Signalling which path ran is what makes that safe.

**Follow-ups.** "Why 500 characters?" · "What are the citations?" · "Why the 50-character floor?"

**What interviewers expect.** The 50-character check explained as failure detection rather than
validation — a PDF that is a scanned image extracts to nothing, and returning "could not extract
text" is far better than sending an empty context to the model and getting a confident
hallucination.

---

### Q226. What is the biggest problem with your RAG implementation?

**Ideal answer.** It re-extracts and re-chunks the document on **every single question**. There is
no persisted chunk index — each query reads the file from disk, extracts text, and splits it
again. `Document.feature_vector`, a 512-dimension pgvector column, exists for the visual-search
path and is **not used here at all**. On a backend where requests queue, this is the most
expensive uncached operation in the system, and it is entirely avoidable.

**Why we chose this.** Built for correctness first and never revisited. It works; it is just
wasteful.

**Alternatives.** Chunk and embed at upload; cache extracted text; cache chunks; cache answers.

**Tradeoffs.** The fix is well-scoped: on upload, chunk the document, embed each chunk, store them
in a table with a foreign key to `StudyMaterial` and a pgvector column, and invalidate on
replacement. That converts a per-question extraction into a vector lookup.

**Follow-ups.** "How would you fix it?" · "Why not cache the answer?" · "What would you store?"

**What interviewers expect.** Rejecting answer-caching as the tempting wrong fix: caching by
question hash means two students asking similar questions get identical answers, and the hash does
not capture the context that made the answer right. Caching the *index* is safe; caching the
*output* is not.

---

### Q227. Why `RecursiveCharacterTextSplitter` with 500/50?

**Ideal answer.** Recursive splitting tries progressively finer separators — paragraphs, then
lines, then sentences, then characters — so chunks break at semantic boundaries where possible
rather than mid-sentence. 500 characters is small enough to keep retrieved context focused and
large enough to contain a complete idea; the 50-character overlap ensures a concept spanning a
boundary appears intact in at least one chunk.

**Why we chose this.** Overlap is the cheap insurance against the boundary problem, which is the
main failure mode of naive fixed-size chunking.

**Alternatives.** Fixed-size; sentence-based; semantic chunking with an embedding model;
document-structure-aware splitting.

**Tradeoffs.** 500 characters is small for technical material where a code block or a full
definition may exceed it, and the chunk size was not tuned against retrieval quality — it is a
reasonable default rather than a measured optimum.

**Follow-ups.** "How would you tune the size?" · "What is the boundary problem?" · "What about
code blocks?"

**What interviewers expect.** Admitting the parameters are defaults, not measurements, and naming
how you would tune them: a small evaluation set of question-and-correct-passage pairs, then
measure retrieval recall at k across chunk sizes. Knowing the experiment matters more than having
run it.

---

### Q228. How does retrieval actually work?

**Ideal answer.** For visual search it is genuine vector similarity — `Document.feature_vector`
holds 512-dimension embeddings in pgvector and the query endpoint does a similarity search. For
the RAG doubt path, chunks are produced per request and passed to `RAGService`, so retrieval
operates over a freshly-built chunk set rather than a persistent index. That is the direct
consequence of Q226 — no index means no index lookup.

**Why we chose this.** The visual-search path was built with embeddings; the RAG path was built
around a single document the user has already selected, so retrieval scope is small.

**Alternatives.** Embed all chunks and search top-k; hybrid dense-plus-BM25; rerank with a
cross-encoder.

**Tradeoffs.** Because the user selects a specific material, retrieval scope is one document
rather than a corpus, which is why an in-memory approach is survivable. It would not survive
"search everything I have uploaded."

**Follow-ups.** "Is that really retrieval?" · "What about hybrid search?" · "What if scope grew?"

**What interviewers expect.** Honesty that single-document RAG with per-request chunking is closer
to **long-context stuffing with a filter** than to classical retrieval, and that scaling scope to
a corpus is what would force a real index. Being precise about what your system is, rather than
what the acronym implies, is the answer.

---

### Q229. Why pgvector rather than a vector database?

**Ideal answer.** One datastore, one backup, one connection pool — and critically, the ability to
filter by ownership in the same query as the similarity search. Documents are user-scoped, so a
dedicated vector store would need the access-control model replicated into it and kept consistent.
Neon ships pgvector on the free tier and the corpus is small, so the operational simplicity wins
decisively.

**Why we chose this.** The filtering argument is the strong one. Combining `WHERE user_id = ...`
with vector similarity is awkward across two systems and trivial in one.

**Alternatives.** Pinecone; Weaviate; Qdrant; FAISS in-process; Elasticsearch dense vectors.

**Tradeoffs.** pgvector's ANN indexes are good but not best-in-class at very large scale, and
building HNSW indexes costs memory — a real constraint at 512 MB. Below roughly a million vectors
the tradeoff strongly favours Postgres.

**Follow-ups.** "What index do you use?" · "Exact or approximate?" · "When would you move?"

**What interviewers expect.** A straight answer on the index: at this corpus size, exact search is
correct and fast, and claiming an HNSW index you have not built would collapse under one
follow-up. Exact search over a small filtered set is the right engineering, not a shortcut.

---

### Q230. How do you prevent hallucination?

**Ideal answer.** Partially. The answer is grounded in retrieved chunks and the response carries
citations, so a student can check the source — which is the honest mitigation, since it moves
verification to the user rather than eliminating the failure. The 50-character extraction floor
prevents the worst case of sending empty context and receiving confident invention. What I do not
do is verify that the answer is actually supported by the cited chunks.

**Why we chose this.** Citations are cheap and genuinely useful; automated groundedness checking is
not cheap.

**Alternatives.** Groundedness scoring with a second model; span-level attribution; refusing to
answer below a retrieval-confidence threshold; NLI entailment checks.

**Tradeoffs.** A second model call to verify support would roughly double cost and latency on an
endpoint that is already the most expensive in the system. A retrieval-confidence threshold is the
cheaper partial answer — if the best chunk similarity is low, say so rather than answering.

**Follow-ups.** "Do you verify the citations?" · "What if retrieval returns nothing relevant?" ·
"What is groundedness?"

**What interviewers expect.** Acknowledging that citations are a **trust affordance rather than a
correctness guarantee** — a model can cite a chunk and still state something the chunk does not
support. Being precise about what a mitigation actually mitigates is the recurring theme of a good
security-and-reliability answer.

---

### Q231. How do embeddings work and why `text-embedding-004`?

**Ideal answer.** An embedding maps text into a vector space where semantic similarity corresponds
to geometric proximity, so "how do I reverse a linked list" lands near a passage about pointer
manipulation even with no shared keywords. Gemini's `text-embedding-004` was chosen for the free
tier alongside a model I was already using for vision, keeping the provider count down.

**Why we chose this.** Provider consolidation and cost. The embedding quality difference between
current models is small relative to the chunking and retrieval decisions around it.

**Alternatives.** OpenAI `text-embedding-3`; open models like `bge` or `e5` run locally;
Sentence-Transformers.

**Tradeoffs.** A hosted embedding model means a network call per document and a dependency for
indexing. Local embeddings would remove that and need the torch stack the web tier deliberately
excludes — the same 512 MB constraint that disabled the GCN.

**Follow-ups.** "Why 512 dimensions?" · "Could you run embeddings locally?" · "What is cosine
similarity?"

**What interviewers expect.** The dimension figure tied to storage and index cost — 512 floats per
document, and index build memory scales with it — plus the observation that the same memory
constraint that keeps torch out of the web tier also rules out local embedding models. One
constraint, several consequences.

---

### Q232. How would you evaluate RAG quality?

**Ideal answer.** Separately for the two stages, because they fail differently. Retrieval:
recall at k on a small hand-labelled set of question-to-correct-passage pairs — if the right chunk
is not retrieved, no amount of generation quality helps. Generation: groundedness (is every claim
supported by the retrieved context) and answer relevance, judged by a stronger model or a human on
a golden set. I have neither, and retrieval recall is the one I would build first because it is
cheap and diagnostic.

**Why we chose this.** Diagnosing which stage is broken is impossible from an end-to-end score
alone.

**Alternatives.** RAGAS; end-to-end human ratings; user thumbs-up feedback.

**Tradeoffs.** User feedback is free and biased — students rate answers they *like*, which
correlates weakly with correctness, the same problem as engagement metrics in the recommender.

**Follow-ups.** "Why retrieval first?" · "What is recall at k?" · "Would you use an LLM judge?"

**What interviewers expect.** The staged-diagnosis argument: an end-to-end score tells you the
system is bad and not why. Measuring retrieval independently is what makes the failure actionable,
and it needs no LLM to compute.

---

### Q233. What is the difference between RAG and fine-tuning?

**Ideal answer.** RAG injects knowledge at inference time from an external store; fine-tuning bakes
it into weights during training. RAG is right when the knowledge is user-specific, changes
frequently, or needs citation — all three are true of a student's uploaded study materials, which
are private, new, and need to be quotable. Fine-tuning is right for *behaviour* — tone, format,
task-specific style — which is why the one place I would consider it is coach hint generation
rather than content knowledge.

**Why we chose this.** The knowledge here is per-user and arrives after training, so fine-tuning
is structurally impossible for it.

**Alternatives.** Long-context stuffing; a hybrid of both; prompt-based few-shot.

**Tradeoffs.** Long-context models weaken the argument for chunked retrieval when documents are
small — you can fit a whole study note in context and skip retrieval entirely, at higher token
cost. That is a real and increasingly common alternative for single-document cases like this one.

**Follow-ups.** "Would long context replace RAG?" · "Could you do both?" · "What about cost?"

**What interviewers expect.** Engaging with the long-context argument honestly rather than
defending RAG reflexively. For single-document question answering with modest documents,
stuffing the whole thing into context is simpler and often better — and the case for retrieval
strengthens with corpus size, not model weakness.

---

### Q234. How would you scale RAG to thousands of documents?

**Ideal answer.** Persist the index, which is the fix already needed for a single document.
Chunk and embed at upload, store in a chunks table with a pgvector column and a foreign key to the
material, add an HNSW index once the corpus justifies it, and filter by owner in the same query.
At that scale I would add hybrid retrieval — dense vectors plus BM25 keyword matching — because
pure dense search is weak on exact identifiers, and technical material is full of them.

**Why we chose this.** Each step is triggered by a scale threshold rather than adopted upfront.

**Alternatives.** Dedicated vector database; a managed RAG service; per-user indices.

**Tradeoffs.** HNSW build memory is the constraint that would eventually force a move off a small
Postgres instance — index construction is memory-hungry and 512 MB is already tight.

**Follow-ups.** "Why hybrid?" · "When do you leave Postgres?" · "What about reranking?"

**What interviewers expect.** The BM25 argument made concrete: a student searching for a specific
function name or error string is doing lexical matching, and dense embeddings are systematically
weak there. Naming the *failure mode* that motivates hybrid search beats naming the technique.

---

## Section V — Prompt Engineering & Content Generation (Q235–Q242)

---

### Q235. How do you design prompts for structured output?

**Ideal answer.** Explicit schema in the prompt, then validate everything that comes back and
retry on failure. The prompt specifies the JSON shape; `_valid_test_cases` enforces it, including
the string type-check on `stdin` and `expected_output`. The operating assumption is that the
prompt is a *request*, not a contract — the model will occasionally ignore it, and the code must
survive that without persisting bad data.

**Why we chose this.** Treating prompt compliance as guaranteed is the single most common way LLM
integrations break in production.

**Alternatives.** JSON mode; function calling; grammar-constrained decoding; Pydantic with
instructor-style retries.

**Tradeoffs.** Provider JSON mode guarantees syntax and not semantics — valid JSON with an integer
where a string belongs still breaks grading. So validation is required either way, and JSON mode
would reduce retries without removing the need for checks.

**Follow-ups.** "Why not JSON mode?" · "How do you handle partial output?" · "What is in the
prompt?"

**What interviewers expect.** The syntax-versus-semantics split again, now applied to prompting.
It is the same distinction as Q217 and repeating it consistently across contexts shows it is a
principle rather than a memorised fact.

---

### Q236. How did you handle adding a new language to generation?

**Ideal answer.** Adding C required changes in three places, which is itself the lesson. The prompt
needed `"c"` added to the language list. The stub validator needed the language-aware exemption,
because C has no `Solution` class and the universal check rejected every valid C stub. And the
frontend needed C in `SELF_CONTAINED_LANGUAGES` with a full `main()` skeleton, since there is no
generic C wrapper and the student writes a complete program.

**Why we chose this.** It surfaced that language support is not a single list — it is a set of
coupled assumptions spread across prompt, validation, wrapper, and UI.

**Alternatives.** A language registry object; a config file; per-language plugin modules.

**Tradeoffs.** A single language registry defining prompt fragment, validator, wrapper strategy
and UI behaviour would make adding a language one change instead of three. The current spread is
exactly how the `js`/`javascript` bug happened — two places disagreeing about one language.

**Follow-ups.** "Would you build a registry?" · "What is `SELF_CONTAINED_LANGUAGES`?" · "How many
places would change?"

**What interviewers expect.** Recognising the coupling as a design smell with a specific fix, and
connecting it to a bug it has already caused. "Three places is two too many, and here is the
incident that proves it" is much better than describing the change neutrally.

---

### Q237. How do you keep generated questions consistent?

**Ideal answer.** Through the schema and the wrapper contract rather than through prompt wording.
Every question must produce `hidden_test_cases` with string `stdin` and `expected_output`, and a
`boilerplate_code` map whose stubs match what the wrapper will invoke. `wrapper_contract.py`
checks the second mechanically: `template_declaration` extracts what a template declares,
`wrapper_call_contract` describes what the wrapper calls, and `check_pair` asserts they match.

**Why we chose this.** Consistency enforced by a checker survives prompt changes and model
upgrades; consistency requested in a prompt does not.

**Alternatives.** Stricter prompts; human review; a single template per topic.

**Tradeoffs.** The checker itself needed tests — my first version counted Python's `self` as a
parameter and false-positived on every Python template. Making it language-aware took four new
tests. **The tool that catches your bugs needs its own tests.**

**Follow-ups.** "What does the checker check?" · "How did it false-positive?" · "Does it run in
CI?"

**What interviewers expect.** The false-positive story, because it is a good second-order lesson:
a validation tool with a bug is worse than no tool, since it produces confident wrong signals
about your content. And `audit_wrapper_templates` runs it read-only against production data.

---

### Q238. What is prompt injection and are you vulnerable?

**Ideal answer.** Prompt injection is untrusted input containing instructions that the model
follows as if they came from the developer. SparkLM has two exposures. Student questions go into
the RAG prompt, so a student could try to override the system instruction — the impact is limited,
since the worst outcome is a bad answer to themselves. The more interesting one is **uploaded
documents**: a student could upload a file containing instructions, and those chunks get retrieved
and placed into the prompt. That is indirect prompt injection, and I do not defend against it.

**Why we chose this.** Not chosen — it is an unmitigated exposure, and the reason it is low
severity is that the blast radius is the attacker's own session.

**Alternatives.** Delimiter-based separation with explicit instruction hierarchy; input filtering;
a separate model to detect injection; output constraints.

**Tradeoffs.** Injection defences are imperfect and add latency. Here the containment is
structural: the LLM has no tool access, cannot write to the database, and its output is displayed
to the same user who supplied the input.

**Follow-ups.** "Why is it low severity?" · "What if the LLM had tools?" · "What about shared
documents?"

**What interviewers expect.** The severity reasoning done properly — impact depends on what the
model can *do*, and this one can only produce text for the person attacking it. Then the
conditional: if study materials became shareable between students, or if the model gained tool
access, this immediately becomes serious. Threat models are about capability, not just
possibility.

---

### Q239. How do you handle LLM output rendered to users?

**Ideal answer.** This is the XSS surface that matters most in the frontend. React escapes by
default, so the risk concentrates on any `dangerouslySetInnerHTML` and on markdown rendering — and
a model can be prompted to emit markup. Combined with tokens stored in `localStorage`, a
successful injection here would be full account takeover rather than a contained nuisance.

**Why we chose this.** It is the intersection of two separately-manageable weaknesses, which is
what makes it the highest-severity compound risk in the system.

**Alternatives.** Sanitise with DOMPurify; render markdown with a strict allowlist; a Content
Security Policy; plain text only.

**Tradeoffs.** A strict CSP is the highest-value addition — it is a header rather than a refactor,
and it turns a successful XSS into a mostly-inert one by blocking inline script and restricting
`connect-src`. I do not have one.

**Follow-ups.** "Do you sanitise?" · "What is the compound risk?" · "Why does CSP help?"

**What interviewers expect.** Chaining the two weaknesses into one severity assessment — LLM
output is semi-trusted content, rendering it unsanitised is an XSS vector, and `localStorage`
tokens turn XSS into account takeover. Compound-risk reasoning is exactly what a security
interviewer is probing for.

---

### Q240. How do you version prompts?

**Ideal answer.** I do not, and it is a real gap. Prompts are string literals in
`groups/ai_services.py`, so they are versioned by git and nothing else — there is no prompt
identifier stored on generated content, so I cannot tell which prompt produced which question.
When a prompt improves, I have no way to identify which existing questions were generated by the
old one and would benefit from regeneration.

**Why we chose this.** Expedience. It only becomes painful once you want to regenerate
selectively.

**Alternatives.** A prompt registry with IDs; store `prompt_version` on generated rows; a prompt
management service.

**Tradeoffs.** The fix is small — a version constant recorded on each generated question — and it
is the same shape as the `policy_version` column the recommender needs and also does not have.
Neither is built, which is the more interesting observation.

**Follow-ups.** "How would you regenerate selectively?" · "What is a prompt registry?" · "Is
anything in the system versioned this way?"

**What interviewers expect.** The pattern recognised as generally missing rather than
inconsistently applied: **nothing in SparkLM records which version of a decision-maker produced
a given artifact** — not prompts for questions, not policies for recommendations. Both are
planned, both are cheap, and both become impossible to reconstruct retroactively. Spotting one
absence as an instance of a category is the answer.

---

### Q241. What makes a good prompt for test-case generation?

**Ideal answer.** Explicit types, explicit format, and explicit edge-case coverage. The failures I
actually hit were type failures — integers where strings were expected — and literal `\n` in
generated stdin, which the grader now converts to real newlines with
`tc.get('stdin', '').replace('\\n', '\n')`. So a good prompt is one whose *observed* failure modes
have been folded back into it, and whose remaining failure modes are caught by validation.

**Why we chose this.** Prompts should be written against observed failures, not imagined ones.

**Alternatives.** Few-shot examples; property-based specification; generate cases from a reference
solution.

**Tradeoffs.** Generating test cases from a *reference solution* rather than from the problem
statement would be far more reliable — run a known-correct solution against generated inputs and
record the outputs as expected. That eliminates the semantically-wrong-expected-output failure
entirely, and it is the strongest unimplemented improvement in the pipeline.

**Follow-ups.** "How do you cover edge cases?" · "Why not use a reference solution?" · "What is
the `\n` issue?"

**What interviewers expect.** The reference-solution proposal, because it reframes the problem:
stop asking the model for *answers* and ask it only for *inputs*, then compute the answers with
code. Moving work from the unreliable component to the reliable one is the right instinct.

---

### Q242. What have you learned about working with LLMs?

**Ideal answer.** Three things. Validate everything, because the failure mode is plausible output
rather than an error — the same lesson as the ML section. Fall back narrowly, because a broad
fallback hides faults and doubles failure latency. And move as much work as possible from the
model to deterministic code: generating test-case *inputs* and computing outputs with a reference
solution is strictly better than asking the model for both, and my content pipeline does the
weaker thing.

**Why we chose this.** Each was learned from a specific failure rather than read.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "Which was most expensive?" · "What surprised you?" · "Would you use LLMs again?"

**What interviewers expect.** The convergence: LLM failures and ML failures are the same category
as the silent-cache and dead-validator failures — something produces confident, well-formed,
wrong output and nothing complains. Being able to say the entire project taught one lesson in five
different domains is a genuinely strong closing statement.

---

## Section W — Judge0 & Sandboxed Execution (Q243–Q248)

---

### Q243. How do you safely execute untrusted student code?

**Ideal answer.** By never executing it. Student code is string-templated into a language-specific
wrapper and shipped to Judge0 over HTTPS, base64-encoded, with `wait=true` and a 15-second
timeout. There is no `eval`, no `exec`, no subprocess anywhere in the Django process. A sandbox
escape is Judge0's problem, and the blast radius of a compromise stops at their infrastructure
rather than mine.

**Why we chose this.** Arbitrary code execution is the dominant risk in this product category, and
the only robust answer is not to be the one executing it.

**Alternatives.** Self-hosted Judge0; containers per submission; gVisor or Firecracker; static
analysis and refuse to run.

**Tradeoffs.** Outsourcing means trusting a third party with student code and depending on their
availability — which is exactly why grading has no fallback and returns 503 rather than degrading.
The architecture specifies a self-hosted isolated fleet for a later phase.

**Follow-ups.** "What if Judge0 is compromised?" · "Is the wrapper a security boundary?" · "Would
you self-host?"

**What interviewers expect.** An unambiguous "no" on the wrapper being a security boundary. It is
a **templating** mechanism for correctness — it does not sanitise anything, and treating it as
protection would be dangerous. Being precise about which of your components are security controls
and which merely look like them is the answer.

---

### Q244. Walk me through the wrapper system.

**Ideal answer.** One canonical test-case format — `stdin` and `expected_output` — has to drive six
languages, so the wrapper is the adapter. Python, Java and JavaScript have generic wrappers that
use runtime reflection to find and invoke the student's solution method. C and C++ have **no
wrapper**: the student writes a complete self-contained program. Selection goes through
`wrapper_for(question, lang_key)`, which prefers a per-question wrapper stored on the row and
falls back to the generic one by language.

**Why we chose this.** Reflection means one wrapper per language rather than one per question, and
generating a wrapper per question would multiply the content pipeline's failure surface.

**Alternatives.** Per-question wrappers everywhere; a uniform stdin/stdout contract with no
wrapper; language-specific test harnesses.

**Tradeoffs.** C and C++ having no generic wrapper is a genuine gap — those students do more work
and get less scaffolding, which is why the frontend gives them a full `main()` skeleton rather
than a method stub.

**Follow-ups.** "Why no C wrapper?" · "What is `WRAPPER_LANGUAGE_ALIASES`?" · "Reflection in
Java?"

**What interviewers expect.** The defensive detail in `wrapper_for`: blank strings, `None`, and
non-dict values are all treated as **absent**, because seed data is inconsistent across
generations of the content pipeline. Defensive parsing of your own data is an admission that the
data is untrustworthy, which is honest.

---

### Q245. Tell me about the Judge0 language ID bug.

**Ideal answer.** `LANGUAGE_IDS` maps language names to Judge0 IDs, and it originally contained
`js: 63` but not `javascript`. The serializer validated `javascript`, so every JavaScript
submission hit the unsupported-language path and errored. Two spellings of one language, in two
places, disagreeing. The fix is that both keys now map to 63, and the code carries a comment
recording why.

**Why we chose this.** The same root cause as `WRAPPER_LANGUAGE_ALIASES` and
`BOILERPLATE_KEYS` — language identity is represented as a bare string in several places, so
divergence is inevitable.

**Alternatives.** A single language enum or registry; normalise at the boundary; validate the maps
agree in a test.

**Tradeoffs.** A registry is the right fix and touches the serializer, the ID map, the wrapper
alias table, and the frontend key map. A cheaper interim fix is a test asserting that every
language the serializer accepts has an entry in every downstream map.

**Follow-ups.** "How would you prevent it?" · "Where else does this pattern appear?" · "Is there a
test now?"

**What interviewers expect.** Generalising from one bug to a class: **stringly-typed identity
across module boundaries** produced this bug, the wrapper alias problem, and the boilerplate key
problem. Three symptoms, one cause — and being able to say the interim test is cheap while the
real fix is a registry shows you can scope a fix to the situation.

---

### Q246. How do you decide whether a submission passed?

**Ideal answer.** Two conditions per test case: normalised output equality **and** Judge0
`status_id == 3`. Matching output from a crashed process is not a pass. Then the overall status is
derived by scanning all case results in priority order — 5 is time limit, 6 is compile error, 7
through 12 are runtime errors — and only if none appear does it settle to accepted or wrong
answer. The critical implementation detail is that `status_id` must be carried into each per-case
result, or the scan finds nothing and **every failure collapses to wrong answer**.

**Why we chose this.** A learning platform that reports a compile error as a wrong answer teaches
the wrong lesson. Feedback quality is the product.

**Alternatives.** Trust Judge0's status alone; compare raw output; report pass/fail only.

**Tradeoffs.** Normalisation of line endings and trailing whitespace makes grading forgiving,
which is right for learning and wrong for a competitive judge where exact output is part of the
problem.

**Follow-ups.** "Why normalise?" · "What is status 11?" · "How did you find the collapse?"

**What interviewers expect.** That the missing-field bug caused a **silent degradation in feedback
quality** rather than an error — nothing crashed, students just got worse information. That is the
hardest class of bug to find and it is the same category as everything else in this project.

---

### Q247. Why is grading throttled at 10 per minute?

**Ideal answer.** Two reasons at once. Judge0 is a metered paid API, so unbounded submissions are
a direct cost. And one submission **fans out** to N Judge0 calls, one per hidden test case, so the
request-level limit understates the downstream load — 10 submissions with 5 cases each is 50
external calls. The scope is shared between `/code/run/` and `/code/submit/` because both consume
the same quota.

**Why we chose this.** The limit protects a third-party budget, which is a different motivation
from the auth throttle protecting against credential stuffing — same mechanism, different purpose.

**Alternatives.** Per-user quotas; cost-based limiting; a queue with a worker pool.

**Tradeoffs.** A fixed request limit ignores fan-out variance — a question with 20 test cases costs
four times one with 5. Cost-based limiting keyed on expected calls would be fairer and more
complex.

**Follow-ups.** "What about fan-out variance?" · "Why share the scope with run?" · "What if Judge0
is slow?"

**What interviewers expect.** The bulkhead connection: a hung Judge0 call holds a worker and a
database connection, so ten simultaneous hangs would exhaust the pool of ten and take down
endpoints unrelated to grading. The throttle is incidentally limiting that blast radius, and a
proper bulkhead would do it deliberately.

---

### Q248. What happens when Judge0 fails?

**Ideal answer.** `_run_on_judge0` catches `requests.Timeout` and `RequestException` and returns
`{"error": ...}`. `GradingService` converts that into a `GradingUnavailable` exception, and the
view maps it to **503** with details. Not a 500 — 500 means I have a bug, 503 means a dependency
is down, and conflating them makes error dashboards useless. Grading is the one dependency with no
fallback, because grading without execution is meaningless.

**Why we chose this.** Fail the feature honestly rather than degrading into a fabricated verdict.

**Alternatives.** Retry with backoff; a circuit breaker; queue for later; return a provisional
result.

**Tradeoffs.** No circuit breaker means a persistently-down Judge0 costs 15 seconds per request,
and on a serialised worker ten queued submissions is 150 seconds of blocked capacity. A breaker is
the correct next addition and I would name it as such.

**Follow-ups.** "Why not retry?" · "What about a circuit breaker?" · "Why no fallback?"

**What interviewers expect.** The 500-versus-503 distinction as an operational argument rather
than a pedantic one — the split is what makes on-call triage possible, and it is the same
reasoning as every other error-classification decision in the system.

---

## Section X — System Design Synthesis (Q249–Q254)

---

### Q249. Design SparkLM from scratch for 100,000 users.

**Ideal answer.** Keep the domain model — Elo skill estimation, per-topic mastery, the curriculum
DAG — and finish the two pieces of it that are specified but unbuilt: two-sided item calibration
and propensity logging. Then change the execution architecture entirely. API tier: stateless ASGI
behind a load balancer, multiple instances, N workers each. Grading asynchronous through Celery
with a per-user routing key so one user's submissions serialise without blocking anyone else, and
results pushed over WebSocket with polling fallback. Postgres primary with a read replica for
dashboards and leaderboards, submissions partitioned by month with archival to object storage.
Redis split into cache-plus-channels and a separate broker. CDN in front for static assets and
edge rate limiting. Prometheus and Grafana, structured logs, Sentry.

**Why we chose this.** It is the architecture already specified for later phases — the current
deployment is a stage of it, not a different design.

**Alternatives.** Microservices; serverless grading; managed platform.

**Tradeoffs.** Async grading changes the client contract from synchronous to eventual, which is
real frontend work: submission states become `queued → running → verdict`.

**Follow-ups.** "What is the per-user routing key for?" · "Where does the DAG live?" · "What
breaks first at that scale?"

**What interviewers expect.** The per-user Celery routing key explained as making row locks
belt-and-braces rather than the primary mechanism — if one user's grading jobs are serialised by
the queue, the `select_for_update` contention largely disappears. Using the queue topology to
solve a concurrency problem is a nice piece of design.

---

### Q250. What would you keep and what would you throw away?

**Ideal answer.** Keep: the engine/service/view layering, because it is what makes 220 tests run
offline in 41 seconds. The runs-test router. Partitioning from day one. The index catalog as a
governed artifact. Throw away: `localStorage` tokens, the unversioned API, denormalised counters
maintained by hand, CASCADE deletes on questions in favour of soft deletes, and the
stringly-typed language identity that has now caused three separate bugs. And **do earlier what
I deferred**: propensity logging on day one, because unlike everything else on these lists it
cannot be retrofitted — every request served without it is permanently unevaluable.

**Why we chose this.** The keeps are structural decisions that constrained everything downstream
positively. The discards are expedient choices that constrained it negatively.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "Why is layering the most valuable?" · "What is the counter drift?" · "Would you
keep Django?"

**What interviewers expect.** A clear criterion separating the lists rather than an arbitrary
split. Mine: keep the decisions that were **hard to reverse and turned out right** — partitioning
is the exemplar, since retrofitting it means a full table rewrite under lock. Discard the ones
that were **easy to make and are now hard to undo** — the unversioned API is the exemplar. And
the third category is the sharpest: things that were **easy to skip and are now impossible to
recover**, which is propensity logging and nothing else on the list.

---

### Q251. How would you make this multi-tenant?

**Ideal answer.** It is already partly there — `CodingPortal` groups topics into curricula, so the
content dimension exists. True multi-tenancy would need a tenant on `User` and on every
learner-state row, tenant-scoped querysets enforced by a manager rather than by remembering to
filter, and per-tenant configuration for throttle rates and feature flags. The dangerous part is
that a single missed filter leaks another tenant's data, so it must be enforced structurally — a
default manager that always filters — rather than by discipline.

**Why we chose this.** Not implemented; portals are content grouping, not isolation.

**Alternatives.** Schema per tenant; database per tenant; row-level security in Postgres.

**Tradeoffs.** Postgres row-level security enforces isolation in the database rather than the
application, which is far stronger — a missed filter cannot leak anything. It costs query planning
complexity and is harder to debug.

**Follow-ups.** "Row-level security or app-level?" · "What about the leaderboard?" · "How do you
test isolation?"

**What interviewers expect.** Naming the shared resources that break tenancy: the global
leaderboard, the shared question bank, and Elo ratings calibrated across the whole population.
Multi-tenancy is not just filtering rows — it is deciding which *models* are global and which are
scoped, and Elo calibration is genuinely ambiguous.

---

### Q252. What is the hardest engineering problem you solved here?

**Ideal answer.** Determining why 40 concurrent authentication requests took production down, and
specifically getting past a hypothesis that was well-supported and wrong. Argon2 at 19 MiB times
40 concurrent is 760 MiB against a 512 MB limit — tidy, plausible, and I confirmed the mechanism
with a barrier-synchronised experiment showing linear 19.1 MiB per hash scaling. Then a clean
control — 40 concurrent requests to a hash-free endpoint completing in 3.1 seconds with zero
failures — killed it. The instance handles the concurrency fine. That sent me to the threading
model, where a sleeping-view experiment showed peak overlap of one at every level.

**Why we chose this.** It is the problem where measurement discipline mattered most, because the
wrong answer was more attractive than the right one.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "How long did it take?" · "What was the earlier contaminated control?" · "What did
you change as a result?"

**What interviewers expect.** The principle stated explicitly: **confirming a mechanism is not
confirming a cause.** The barrier experiment produced a true measurement and I drew a false
conclusion from it. That sentence is the single most valuable thing you can say in a technical
interview about this project.

---

### Q253. What are you least proud of?

**Ideal answer.** Two things. I took production down while load testing and only flagged the risk
afterwards — the test was defensible and the finding was worth having, but not warning anyone
first was a judgement failure, not a technical one. And I shipped a Java template for the
deterministic first problem every new user receives without ever executing it, so a student's
first interaction with the platform was a guaranteed compile error. I wrote it against the problem
statement rather than against the wrapper.

**Why we chose this.** Both produced permanent process changes — capped concurrency with clean
controls, and `wrapper_contract.py` with an audit command.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "What did you change?" · "Would you load test production again?" · "How do you
prevent the second?"

**What interviewers expect.** Answering without either minimising or over-apologising. Name the
failure, name what changed, move on. The template one is better to lead with because the lesson
generalises — *a template that has never been executed is not a template, it is a guess* — and
because it produced a tool rather than just a resolution.

---

### Q254. Why should we hire you based on this project?

**Ideal answer.** Because I can show you the difference between what I measured and what I
inferred, and I corrected myself in public four times when the difference mattered. The system
works — Argon2 migration with no downtime, connection pooling that cut database latency 74%,
throttling that went from provably inert to enforcing, 220 tests with mutation verification. But
the more useful thing is the failures: a cache that accepted writes and returned nothing, password
validators that were configured and dead, a test suite that passed while a security control did
nothing, and a hypothesis I confirmed the mechanism for and was still wrong about. I found all of
those because I measured instead of assuming, and I wrote them down instead of quietly fixing
them.

**Why we chose this.** Because the failures are the differentiator. Plenty of candidates have a
project that works.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "What would you do differently?" · "What is the project's biggest weakness?" ·
"What do you want to work on next?"

**What interviewers expect.** Confidence without overclaiming, and a specific, unflattering answer
to the weakness question ready to go: **no in-container observability**, so every memory
conclusion is inference from external symptoms, and one screenshot of the metrics dashboard would
have answered in seconds what took two review rounds to reason out. Closing on the thing you could
not do — rather than the things you did — is what makes the rest credible.

---

## Part 6 Recap — The Last Five Stories

| # | Story | The one-line hook |
|---|---|---|
| 26 | **Narrow fallback beats broad** | Falling back on any error hides transient faults and doubles failure latency; only quota exhaustion is a condition retrying cannot fix. |
| 27 | **Valid JSON, wrong types** | A model returned integers where strings were expected and broke grading — syntactic validity is not semantic validity. |
| 28 | **RAG that re-chunks every question** | No persisted index, and the pgvector column that exists is used by a different feature entirely. |
| 29 | **Two spellings, two places, one language** | `js` and `javascript` both mean Judge0 ID 63, and for a while only one of them was in the map. |
| 30 | **Ask for inputs, not answers** | Generating test-case inputs and computing outputs with a reference solution moves work from the unreliable component to the reliable one. |

---

## Handbook Close — The Whole Thing in One Page

**254 questions across six parts.** If you retain one idea, make it this:

> Every significant failure in SparkLM was **plausible output from a component that had stopped
> working**, and in most cases the tests were green.

| Part | Domain | Failure signature |
|---|---|---|
| 1 | Backend & Concurrency | Silent failures — nothing crashed, nothing logged |
| 2 | Database, Caching, Redis | Wrong instruments — the system was legible, the measurement was not |
| 3 | Auth, JWT, Security | Every control has a cost; the defensible ones are where you can name it |
| 4 | Performance, Scale, Deploy | The gap between a number and a conclusion |
| 5 | ML & Recommendations | Plausible output *is* the failure mode — a bad recommendation looks like a good one |
| 6 | LLMs, RAG, Judge0 | Same lesson again: validate, fall back narrowly, move work to deterministic code |

**The five things to have loaded before any interview:**

1. **The 40-concurrent outage** — right mechanism, wrong cause, corrected by a clean control.
2. **`CONN_MAX_AGE` never worked** — configured, commented, reviewed, and completely inert.
3. **The silent cache** — disabled every rate limit with zero errors and a passing test suite.
4. **Dead password validators** — configuration that is never invoked looks like configuration
   that works.
5. **Testing your own weakness** — a test asserting XFF rotation *evades* the throttle, so the
   limitation cannot be silently changed.

**And the sentence that ties them together, worth saying out loud:**

> *I have more measurements than most projects this size, and the main thing I learned was how
> easy it is to measure correctly and conclude wrongly.*

---

*End of Part 6. Handbook complete — 254 questions, Parts 1–6.*
