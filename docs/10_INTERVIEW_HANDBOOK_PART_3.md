# SparkLM Technical Interview Handbook
## Part 3 — Authentication, JWT & Security

**Questions 85–126 of 254**
**Companion:** Document 05 (Security), Document 01 §10, Document 02 §5
**Previous:** Part 1 (Backend & Concurrency, Q1–42) · Part 2 (Database, Caching & Redis, Q43–84)

---

## Section H — Password Hashing & Credential Storage (Q85–Q100)

---

### Q85. How are passwords stored in SparkLM?

**Ideal answer.** Argon2id at pinned parameters — `time_cost=2`, `memory_cost=19456 KiB`
(19 MiB), `parallelism=1` — defined in `common/hashers.py` as `TunedArgon2PasswordHasher`.
PBKDF2-SHA256 remains second in `PASSWORD_HASHERS` for verification only, because the existing
user base is stored under it. Django tries hashers in order for *hashing* but identifies the
correct one from the stored hash prefix for *verification*, so both populations work
simultaneously and legacy accounts upgrade transparently on their next successful login.

**Why we chose this.** Argon2id is memory-hard, which is the property that matters against
GPU and ASIC attackers. PBKDF2 is only compute-hard, so an attacker with parallel hardware
gets a much better return per dollar.

**Alternatives.** bcrypt (memory-hard-ish at 4 KB, but that is fixed and small); scrypt
(memory-hard, less well-specified for tuning); PBKDF2 with a higher iteration count; Argon2i
or Argon2d.

**Tradeoffs.** Argon2id specifically, rather than Argon2i or Argon2d, because it is the hybrid:
Argon2d is faster but vulnerable to side-channel attacks on the memory access pattern, Argon2i
is side-channel resistant but weaker against time-memory tradeoffs, and Argon2id runs the first
pass in i-mode and the rest in d-mode to get most of both.

**Follow-ups.** "Why is memory-hardness the property that matters?" · "Why keep PBKDF2?" ·
"Argon2i vs id vs d?"

**What interviewers expect.** The memory-hardness argument stated economically: an attacker's
advantage comes from parallelism, and forcing 19 MiB per hash means a GPU with 24 GB can run
roughly a thousand hashes in parallel instead of hundreds of thousands. It converts the
attacker's constraint from compute to RAM, which is much harder to scale cheaply.

---

### Q86. Why not Django's default Argon2 parameters?

**Ideal answer.** Because they would take production down. Django's stock
`Argon2PasswordHasher` uses `memory_cost=102400 KiB` (100 MiB) and `parallelism=8`. The web
tier runs on a 512 MB Render instance with a measured resident set of about 202 MiB, so at
100 MiB per hash, **four concurrent logins exceed the memory limit**. An OOM restarts the
container, and a cold start on this plan was measured at 92.9 seconds — so adopting Argon2
unconfigured would have handed every subsequent visitor a 93-second wait and undone the entire
previous milestone. Separately, `parallelism=8` asks for eight threads on a throttled
fractional vCPU, which adds contention rather than speed.

**Why we chose this.** 19 MiB, `t=2`, `p=1` is OWASP's recommended Argon2id minimum. It is the
point where security guidance and the memory budget intersect, which is a comfortable place to
be — I am not below a published recommendation.

**Alternatives.** Stock defaults on a bigger instance; a lower memory cost with higher time
cost; bcrypt to sidestep the memory question.

**Tradeoffs.** Trading memory cost for time cost is not neutral — memory-hardness is precisely
the anti-GPU property, so lowering memory and raising iterations gives back the advantage
Argon2 exists to provide. I would rather sit at the OWASP floor on memory than exceed it on
time.

**Follow-ups.** "What is the memory budget formula?" · "What if you get a bigger instance?" ·
"Is 19 MiB enough?"

**What interviewers expect.** The formula, because it makes the reasoning checkable:
`202 + 19N MiB` against a 512 MB ceiling. That is derived and test-pinned — there is a test
asserting the configured memory cost fits the instance budget at the target concurrency, and a
companion test asserting the same check would *reject* Django's default. Codifying the
constraint as a failing test rather than a comment is the part worth emphasising.

---

### Q87. How did existing users migrate from PBKDF2 to Argon2?

**Ideal answer.** They did not — Django does it for them, one account at a time, on next
successful login. `AbstractBaseUser.check_password` accepts a setter callback that Django
invokes when the verifying hasher is not the preferred one (`hasher_changed → must_update`).
The setter rehashes with the preferred hasher and persists via
`save(update_fields=["password"])`. No reset emails, no bulk migration, no user-visible step.
Progress is at 1 of 13 migratable accounts today, and it closes as people sign in.

**Why we chose this.** A bulk migration is impossible — you cannot rehash a password you do not
have in plaintext. The only alternatives are forced resets or transparent upgrade, and forced
resets are a support burden and a phishing-training exercise.

**Alternatives.** Force a password reset for everyone; dual-hash on next login and store both;
leave legacy accounts on PBKDF2 forever.

**Tradeoffs.** Transparent migration means an indefinite window where two algorithms coexist,
with the timing consequence covered in Q89. Leaving legacy accounts forever would avoid that
window and permanently retain the weaker algorithm.

**Follow-ups.** "How do you know it is working?" · "What if a user never logs in again?" ·
"How long is the window?"

**What interviewers expect.** That you built a way to observe it: `manage.py
password_hash_status` reports migrated versus awaiting, excludes SSO accounts from the
denominator, and prints "Migration window CLOSED" only when no legacy hashes remain. A
migration you cannot measure is a migration you cannot finish.

---

### Q88. What happens to an account that never logs in again?

**Ideal answer.** It stays on PBKDF2 indefinitely, which is safe — PBKDF2 at 1,000,000
iterations is not broken, just weaker than Argon2id — and the hasher stays listed so it remains
verifiable. The practical consequence is that the migration window may never formally close, so
"remove PBKDF2 once everyone has migrated" is not a plan I can rely on. If I needed to retire
it, the options would be a forced reset for the remaining accounts or accepting that dormant
accounts get locked out and handled by support.

**Why we chose this.** Correctness over tidiness. Removing a hasher that any stored hash still
needs locks those users out silently.

**Alternatives.** Expire dormant accounts; force reset after a deadline; keep both hashers
permanently.

**Tradeoffs.** Keeping PBKDF2 permanently means the verification path retains a weaker
algorithm — but only for accounts already stored under it, and an attacker who can read the
hash column has already won more than the algorithm choice matters for.

**Follow-ups.** "So when do you drop PBKDF2?" · "Is that a security problem?" · "How would you
force the last few?"

**What interviewers expect.** Resisting the tempting wrong answer. "Eventually we remove
PBKDF2" sounds clean and is exactly the change that causes the worst failure in this system.
Knowing when *not* to clean up is a real skill.

---

### Q89. Does the migration create a timing side channel?

**Ideal answer.** Yes, and it is documented rather than pretended away. During the window,
response times differ by account state: a nonexistent user costs about 0.1 s because Django
hashes a throwaway value with the *preferred* hasher; an already-migrated account costs about
0.1 s for the Argon2 verify — those two match, which is the important part; but a
not-yet-migrated account costs around 1.6 s for the PBKDF2 verify at a million iterations. So a
slow response signals "this account exists and has not logged in since the migration."

**Why we chose this.** Accepted, for three stated reasons. It grants no new capability —
`/register/` already reveals whether a username is taken, by design. It closes on its own as
accounts log in. And it leaks account *recency*, never credentials.

**Alternatives.** Pad all responses to a constant time; verify with both hashers always; force
reset to eliminate the window.

**Tradeoffs.** Constant-time padding means every login pays the slowest path, which on a
serialised worker is a throughput disaster. Django itself declines to solve this:
`Argon2PasswordHasher.harden_runtime` is a deliberate no-op, with an upstream comment that it
is "too complicated to implement a sensible hardening algorithm."

**Follow-ups.** "Could you pad the response?" · "What is `harden_runtime`?" · "Was it better
before?"

**What interviewers expect.** The last question is the one to volunteer: **before this
migration every path was PBKDF2-dominated and therefore indistinguishable.** The migration
introduced a signal that did not previously exist. Saying that out loud — that your security
improvement created a small new leak — is much stronger than only presenting the upside.

---

### Q90. What is `fake_runtime` and why does it matter?

**Ideal answer.** When authentication is attempted against a username that does not exist,
Django still runs a hash — `make_password` on a throwaway value using the preferred hasher —
rather than returning immediately. Without it, a nonexistent user would return in microseconds
while a real user costs a full Argon2 verify, and the difference is a trivially measurable
user-enumeration oracle. It matters here specifically because it must use the *same* hasher as
the real path, which is why one of my tests asserts that the preferred hasher and the
fake-runtime cost match.

**Why we chose this.** It is Django's behaviour, but I depend on it and test it rather than
assuming it.

**Alternatives.** Constant-time comparison only; a fixed sleep; not caring.

**Tradeoffs.** A fixed sleep is worse than a real hash — it is guessable, and it does not track
changes to the hasher configuration. Doing the real work is self-maintaining.

**Follow-ups.** "How would you test it?" · "Does it fully close the gap?" · "What about the
database lookup?"

**What interviewers expect.** The honest limitation: it equalises the *hashing* cost, not the
whole request. The user lookup still differs — a hit versus a miss on an indexed query — and
during the migration window the PBKDF2 path dwarfs both. So it narrows the channel rather than
closing it, and combined with a 5-per-minute throttle, enumeration by timing is impractical
here even though it is not impossible.

---

### Q91. Why is rolling back a reorder rather than a removal?

**Ideal answer.** Because once a user has logged in after the migration, their stored hash
**is** Argon2. If `TunedArgon2PasswordHasher` is removed from `PASSWORD_HASHERS`, or
`argon2-cffi` falls out of `requirements.txt`, then `identify_hasher()` raises on those stored
hashes — and Django catches that and reports it as an ordinary **failed login**. Every migrated
account is locked out, users see "wrong password," and nothing in the logs explains why. The
supported rollback is to move PBKDF2 to the front and **leave Argon2 in the list**, so new
hashes use PBKDF2 while migrated accounts remain verifiable.

**Why we chose this.** The failure mode is silent and total, which makes it worth writing down
in three places: the settings comment, the deployment runbook, and two tests.

**Alternatives.** Refuse to allow rollback; bulk-downgrade hashes (impossible without
plaintext); version the hasher list.

**Tradeoffs.** Keeping Argon2 listed during a rollback means the "rolled back" system still
depends on the library you may be rolling back because of. If the rollback reason is a broken
`argon2-cffi` build, the reorder does not save you — you need the library present either way.

**Follow-ups.** "What if the rollback reason is argon2-cffi itself?" · "How is this tested?" ·
"What does the user see?"

**What interviewers expect.** Both tests named:
`test_reordering_keeps_both_populations_working` pins the supported path, and
`test_removing_argon2_locks_out_migrated_users` pins the *failure* — asserting that removal
breaks verification. Testing the disaster, not just the happy path, is what makes the warning
credible.

---

### Q92. You added a boot probe for the hashers. Why, when a test already covers it?

**Ideal answer.** Because a test cannot see a bad deploy. `test_removing_argon2_locks_out_migrated_users`
proves the behaviour in CI, but the failure I am defending against is operational — someone
edits `PASSWORD_HASHERS` in a hurry, or a dependency pin drifts, and the tests that would have
caught it are not the thing running in production. `verify_password_hashers()` runs in
`CommonConfig.ready()` on every process start, confirms the hashers construct and that Argon2
is present, and logs one actionable ERROR naming the consequence if not.

**Why we chose this.** It converts a silent lockout into one line in the deploy log, which is
the same reasoning as the cache probe.

**Alternatives.** Fail fast and refuse to boot; a CI check on the settings file; monitoring
login failure rates.

**Tradeoffs.** Refusing to boot is the tempting answer and it is wrong here: the operator may be
**mid-rollback**, in which case the "wrong" configuration is the intended one. So the probe
distinguishes cases — a supported rollback (PBKDF2 first, Argon2 retained) logs a WARNING, and
only a genuinely missing Argon2 hasher logs an ERROR.

**Follow-ups.** "Why not fail fast?" · "How do you test a probe?" · "What about monitoring
login failures?"

**What interviewers expect.** The rollback distinction, because it shows the probe was designed
against real operational scenarios rather than added reflexively. It is also test-pinned: I
assert that a supported rollback logs exactly one warning and zero errors, so the probe cannot
start crying wolf.

---

### Q93. Walk me through `manage.py password_hash_status`.

**Ideal answer.** It classifies every account into one of four buckets — Argon2 (migrated),
PBKDF2 (awaiting first sign-in), unusable (Google SSO, which never migrates), and UNREADABLE
(no configured hasher can identify the hash). It reports progress as migrated over *migratable*,
excluding SSO from the denominator so the window can actually reach 100%. It exits **2** if any
hash is unreadable, because that is not a progress report — it means those users are locked out
right now. And it notes when disabled accounts exist, because a disabled account still rehashes
on a correct password, so "migrated" does not mean "signed in."

**Why we chose this.** It replaced a seven-line shell snippet in the deployment runbook that
operators were told to paste. That snippet split on `$` to get the algorithm, which mislabelled
SSO accounts and could not detect an unreadable hash at all — the one condition that actually
requires action.

**Alternatives.** Keep the snippet; a Django admin view; a monitoring query.

**Tradeoffs.** A command is versioned and testable and costs more upfront than a snippet. My
rule for when it is worth it: whether the logic contains *judgement*. Counting rows does not;
deciding which rows belong in a migration denominator does.

**Follow-ups.** "Why exit 2?" · "What is an unreadable hash?" · "Why exclude SSO?"

**What interviewers expect.** The self-critical note: I initially shipped a
`--fail-if-incomplete` flag for a scheduled check that does not exist, then removed it in my
own review as speculative. Being able to point at something you deleted from your own work is
worth more than anything you added.

---

### Q94. Why does a disabled account still get rehashed?

**Ideal answer.** Because of evaluation order in `ModelBackend.authenticate()`, which computes
`user.check_password(password) and self.user_can_authenticate(user)`. Python's `and`
short-circuits left to right, so `check_password` — which carries the rehash setter — runs
**first**. A disabled account presented with the correct password is therefore upgraded to
Argon2 even though authentication then fails on `is_active`. It is benign, since the upgrade is
strictly stronger and login still fails, but it means the migration counter can advance for
accounts that cannot sign in.

**Why we chose this.** Not chosen — discovered while writing the disabled-account tests that
two consecutive reviews had flagged as missing.

**Alternatives.** N/A — it is framework behaviour.

**Tradeoffs.** N/A. The response was to document it, pin it with a test, and surface it in the
status command so nobody misreads "12 accounts migrated" as "12 accounts signed in."

**Follow-ups.** "Is that a security problem?" · "How did you find it?" · "What if Django
reorders it?"

**What interviewers expect.** The test comment answers the last one: if the assertion ever
fails, Django has reordered `ModelBackend` and the docstring needs revisiting. Writing a test
whose failure message tells the future reader *what the failure means* rather than just that it
failed is a small thing that reads as experienced.

---

### Q95. How do you validate password strength?

**Ideal answer.** Five validators — `UserAttributeSimilarityValidator`, `MinimumLengthValidator`
at 8, `CommonPasswordValidator`, `NumericPasswordValidator`, and a custom
`PasswordComplexityValidator`. The critical part is that they are invoked explicitly:
`UserSerializer.validate_password()` calls Django's `validate_password()` and converts
`DjangoValidationError` into a DRF `ValidationError`. **DRF does not wire `AUTH_PASSWORD_VALIDATORS`
in automatically.** Before that method existed, every validator was configured, visible,
reviewed — and completely dead. Registration accepted literally any password.

**Why we chose this.** Serializers are the boundary where untrusted input becomes trusted data,
so validation belongs there.

**Alternatives.** Validate in the view; a custom `AbstractUser.save()` hook; client-side only;
zxcvbn-style entropy scoring.

**Tradeoffs.** Composition rules — one uppercase, one digit — are known to be weaker than
length-plus-blocklist, and NIST has moved away from them. `CommonPasswordValidator` (a 20,000-word
blocklist) is doing more real work than the complexity rule. I kept both because the complexity
rule is what users expect to see, which is a UX argument, not a security one.

**Follow-ups.** "Are composition rules good practice?" · "How did you find the dead
validators?" · "What about breached-password checks?"

**What interviewers expect.** Willingness to criticise your own rule. Saying "the complexity
validator is there for user expectations, and the blocklist is what actually helps" shows you
know the current guidance rather than the 2010 guidance. Have Have I Been Pwned's k-anonymity
range API ready as the upgrade.

---

### Q96. Where else could configuration be silently dead?

**Ideal answer.** That is the right question, and it is the generalisation of two separate bugs
I hit. `AUTH_PASSWORD_VALIDATORS` was configured and never invoked. The cache was configured and
never persisted. Both looked correct in review, both had passing tests, and both were doing
nothing. The category is: **configuration that is never invoked is indistinguishable from
configuration that works.** My response is boot probes for the two I know about, and a general
habit of asking, for any security control, "what test would fail if this were removed
entirely?" If the answer is none, the control is decorative.

**Why we chose this.** Two incidents in the same category is a pattern, not bad luck.

**Alternatives.** Audit every setting; integration tests for every control; startup assertions
for everything.

**Tradeoffs.** Probing everything at boot costs startup time and produces noise. I probe the two
controls whose silent failure is both plausible and severe.

**Follow-ups.** "What else would you probe?" · "How do you audit for this?" · "Is `NUM_PROXIES`
one?"

**What interviewers expect.** A candidate answer. `REST_FRAMEWORK["NUM_PROXIES"]` is
deliberately retained in settings but **bypassed** by every current throttle class — so it is
live configuration that affects nothing. It is documented as such rather than removed, because
it is the correct setting again behind a stable proxy. That is dead configuration by design,
which is fine as long as it is labelled.

---

### Q97. How do you handle the `SECRET_KEY`?

**Ideal answer.** It comes from the environment with a development fallback, and there is a
boot guard: `_guard_production_secret_key()` raises `ImproperlyConfigured` if
`DJANGO_DEBUG=false` and the key is still the well-known dev value. The reason it deserves a
guard rather than a comment is blast radius — SimpleJWT signs with `SECRET_KEY`, so a leak
simultaneously compromises session cookies, password-reset tokens, and **every issued JWT**.
Three failure domains behind one string.

**Why we chose this.** Making the mistake impossible beats documenting it.

**Alternatives.** Separate signing keys per purpose; a secrets manager; key rotation with a
grace window.

**Tradeoffs.** A distinct `SIMPLE_JWT["SIGNING_KEY"]` would decouple JWT signing from session
security, so rotating one would not invalidate the other. That is a genuine improvement I have
not made — today they are the same key, so rotating it logs out every user *and* invalidates
every reset link.

**Follow-ups.** "How do you rotate it?" · "Why not separate keys?" · "What is the blast
radius?"

**What interviewers expect.** The rotation answer is honest and uncomfortable: rotation is
manual and disruptive, and there is no key-versioning scheme that would let old tokens verify
during a grace window. Naming that as the gap — and naming asymmetric signing as the structural
fix (see Q104) — is better than claiming a rotation process you have not built.

---

### Q98. Have you ever exposed a credential?

**Ideal answer.** Yes. A full plaintext environment dump — database password, Django
`SECRET_KEY`, Groq, Gemini, Judge0 and Redis credentials — ended up in a working context during
debugging. Not through an attack; through convenience, which is how most real leaks happen. The
response was to remove the file and rotate. Separately and earlier, a live NVIDIA NIM API key
was briefly committed into a git-tracked `.env.example`; I moved it to the gitignored `.env`,
restored the placeholder, and verified with `git diff` that the tracked file was clean.

**Why we chose this.** Rotation is the only real remedy. Deleting a leaked secret does not
un-leak it.

**Alternatives.** N/A.

**Tradeoffs.** The `.env.example` incident is the more instructive one, because the file
*looked* like a template. A file whose entire purpose is to hold placeholders is exactly where a
real value hides best — nobody reads it closely in review.

**Follow-ups.** "How would you prevent it?" · "What if it had been pushed?" · "Do you scan for
secrets?"

**What interviewers expect.** Prevention specifics: pre-commit hooks with `gitleaks` or
`detect-secrets`, GitHub push protection, and CI secret scanning — none of which I currently
have, and all of which are cheap. Candidates who have actually handled an exposure talk about it
differently from candidates who have only read about it; the tell is that you talk about
rotation first and deletion second.

---

### Q99. How do you know your dependencies are not vulnerable?

**Ideal answer.** I checked `argon2-cffi` 25.1.0 and its bindings against the OSV vulnerability
database during Milestone 3 and got zero results. That is a point-in-time check on the
dependency I was introducing, not continuous monitoring — I do not have Dependabot or a
scheduled audit wired up, which is a real gap given that a Python service has a long transitive
dependency tree.

**Why we chose this.** I checked the thing I was adding because adding a cryptographic
dependency deserves a look. I did not generalise it, and I should have.

**Alternatives.** Dependabot; `pip-audit` in CI; Snyk; renovate with automerge for patches.

**Tradeoffs.** Automated scanning generates noise — most advisories in a transitive tree are not
reachable from your code — and triaging it is real work. `pip-audit` in CI is the cheapest
meaningful step and I would add it first.

**Follow-ups.** "Why not Dependabot?" · "What is OSV?" · "How would you triage findings?"

**What interviewers expect.** Not claiming a security posture you do not have. "I checked the
one I added, and continuous scanning is a gap I would close with `pip-audit` in CI" is a better
answer than implying a process exists.

---

### Q100. Why did you pin `argon2-cffi` and comment the pin so heavily?

**Ideal answer.** Because removing it has the same effect as removing the hasher from
`PASSWORD_HASHERS` — `identify_hasher()` raises on every `argon2$` hash and every migrated user
is locked out, reported as ordinary failed logins. The requirements comment says so explicitly:
do not remove while any stored hash starts with `argon2$`. The pin also notes that the package
ships `abi3` manylinux wheels, so Render's builder needs no compiler — which is why adding a
memory-hard hasher did not require changing the build.

**Why we chose this.** A dependency whose removal causes a silent, total authentication failure
is not an ordinary dependency, and the requirements file is where someone pruning dependencies
will actually look.

**Alternatives.** A comment only in settings; no comment; vendoring the library.

**Tradeoffs.** Heavy comments in `requirements.txt` are unusual and some reviewers dislike them.
The counter is that this is the file a person edits when they are trying to shrink an image or
resolve a conflict, and that is precisely the moment they need the warning.

**Follow-ups.** "What is abi3?" · "What if the wheel is unavailable for a platform?" · "Would a
lock file be better?"

**What interviewers expect.** The `abi3` detail, because it is a concrete deployment
consideration most people never think about: a single wheel works across Python 3.x versions
without recompilation, so the free-tier builder — which has no toolchain and a build timeout —
can install it. Choosing a dependency partly for its *packaging* is a real constraint on a
constrained platform.

---

## Section I — JWT & Session Management (Q101–Q112)

---

### Q101. Why JWTs rather than sessions?

**Ideal answer.** Statelessness. The API serves a separately-hosted SPA on Vercel, so
cookie-based sessions would mean cross-origin cookies with `SameSite=None`, third-party cookie
restrictions, and CSRF on every state-changing request. JWTs sidestep that: the client sends a
Bearer header, there is no server-side session store, and horizontal scaling requires no sticky
sessions or shared session backend. Access tokens live 60 minutes, refresh tokens one day,
signed HS256 with `SECRET_KEY`.

**Why we chose this.** The cross-origin deployment split made it the path of least resistance,
and statelessness is genuinely useful for the multi-instance future the architecture targets.

**Alternatives.** Django sessions with a shared Redis store; opaque tokens with a server-side
lookup; PASETO.

**Tradeoffs.** The classic one: **you cannot revoke a JWT.** A stolen access token is valid for
up to 60 minutes and there is nothing I can do about it, because verification is a signature
check with no database round-trip. Sessions are revocable instantly. I traded revocability for
statelessness, and at this scale that is defensible; for a system handling payments it would
not be.

**Follow-ups.** "How do you revoke?" · "Is 60 minutes too long?" · "What about a blacklist?"

**What interviewers expect.** That you name revocation as the cost immediately rather than
being led to it, and that you know SimpleJWT ships a blacklist app you have chosen not to
enable — because enabling it reintroduces a database lookup per request and therefore most of
the statelessness you bought.

---

### Q102. Walk me through what happens when a JWT arrives.

**Ideal answer.** `JWTAuthentication` extracts the Bearer token, decodes and verifies the HS256
signature against `SECRET_KEY`, checks `exp`, then does `SELECT user WHERE id = payload['user_id']`
and checks `is_active`. That user lookup is the part people forget: **every authenticated
request costs one database query**, because SimpleJWT does not cache the user object. On a
serialised worker at roughly 33 ms per query, that is a measurable fixed cost on every protected
endpoint.

**Why we chose this.** The lookup is what makes deactivation effective — without it, a disabled
user's unexpired token would keep working. So the query buys back a little of the revocability
that JWTs give up.

**Alternatives.** Trust the token entirely and skip the lookup; cache the user in Redis;
embed claims (role, permissions) in the token.

**Tradeoffs.** Embedding claims removes the lookup and makes the token authoritative for
authorisation — which means a permission change does not take effect until the token expires.
Caching the user in Redis trades a Postgres round-trip for a Redis one, which is cheaper but
still a round-trip, and adds an invalidation problem.

**Follow-ups.** "Could you skip the lookup?" · "How much does it cost?" · "What if you cached
the user?"

**What interviewers expect.** Awareness that "stateless" is a half-truth in practice — the token
is stateless, the request is not. Being able to quantify the residual cost, and explain what
it buys (deactivation taking effect immediately), is better than reciting the marketing version.

---

### Q103. Why 60-minute access tokens and 1-day refresh tokens?

**Ideal answer.** The access lifetime bounds the damage from a stolen token — 60 minutes is the
window an attacker gets. The refresh lifetime bounds how long a user stays signed in without
re-authenticating. Sixty minutes is on the long side; 15 minutes is a more common choice and
would be strictly better security. I chose 60 partly for a reason I should be honest about: on
a backend where every request queues, a refresh every 15 minutes across an active user base is
real load, and the refresh endpoint has its own throttle bucket precisely because that traffic
is significant.

**Why we chose this.** A defensible balance, with the security cost acknowledged rather than
hidden.

**Alternatives.** 15-minute access with rotating refresh; sliding sessions; long-lived tokens
with a revocation list.

**Tradeoffs.** Shorter access tokens mean more refresh traffic on a throughput-constrained
backend. Refresh rotation with reuse detection is the real upgrade — issue a new refresh token
on every use, and if an old one is presented again, treat it as theft and invalidate the family.
That requires server-side state, which is the same tradeoff as Q101.

**Follow-ups.** "Would you shorten it?" · "What is refresh rotation?" · "How do you detect
theft?"

**What interviewers expect.** Refresh-token rotation with reuse detection, described correctly.
It is the standard answer to "JWTs cannot be revoked" and shows you know the current practice
rather than the 2015 practice.

---

### Q104. Why HS256 rather than RS256?

**Ideal answer.** HS256 is symmetric — the same secret signs and verifies — which is fine when
one service does both, as here. RS256 is asymmetric: a private key signs, and anyone with the
public key can verify without being able to mint tokens. The moment there is a second service
that needs to validate tokens, RS256 becomes correct, because otherwise you are distributing a
signing secret to every verifier and any one of them can forge.

**Why we chose this.** One service, one key, no distribution problem. HS256 is also faster,
though not enough to matter here.

**Alternatives.** RS256; ES256 (smaller signatures, faster verification); PASETO.

**Tradeoffs.** HS256 compounds the `SECRET_KEY` blast-radius problem from Q97 — because
`SIGNING_KEY = SECRET_KEY`, the JWT signing key is also the session and reset-token key.
Splitting to RS256 with a dedicated keypair would decouple them and enable rotation with a
grace window, since you can publish two public keys during a rollover.

**Follow-ups.** "When would you switch?" · "What is key rotation with RS256?" · "What is the
`alg: none` attack?"

**What interviewers expect.** The `alg: none` and algorithm-confusion attacks, because they are
the classic JWT interview questions. `alg: none` is a token declaring no signature; algorithm
confusion is submitting an HS256 token signed with the RSA *public* key to a server expecting
RS256, which naively verifies it. Both are mitigated by pinning the expected algorithm rather
than trusting the header — which PyJWT and SimpleJWT do by default.

---

### Q105. Where does the frontend store the token, and is that right?

**Ideal answer.** `localStorage`, and no — it is a known divergence from my own architecture
spec, which says access tokens should be held in memory with the refresh token in an httpOnly
cookie, and explicitly marks localStorage as deprecated. The exposure is XSS: any injected
script can read `localStorage` and exfiltrate the token, whereas an httpOnly cookie is invisible
to JavaScript. It is recorded in the architecture document's divergence list rather than quietly
ignored.

**Why we chose this.** Expedience. localStorage survives page reloads with no refresh dance and
no cookie configuration across origins.

**Alternatives.** In-memory access token plus httpOnly refresh cookie (the specified design);
httpOnly cookies for both with CSRF tokens; the BFF pattern.

**Tradeoffs.** The specified design is meaningfully better and costs real work: cross-origin
cookies need `SameSite=None; Secure`, the cookie must be scoped to the API origin, and a page
reload needs a silent refresh to repopulate the in-memory access token. On the current
deployment split that is not trivial.

**Follow-ups.** "So you are vulnerable to XSS?" · "What does httpOnly actually protect?" ·
"What is a BFF?"

**What interviewers expect.** Precision about what httpOnly buys. It does **not** prevent XSS —
an attacker with script execution can still make authenticated requests from the victim's
browser. It prevents *token exfiltration*, so the attack cannot outlive the session or move to
the attacker's machine. That distinction is frequently muddled and getting it right is a good
signal.

---

### Q106. How does the frontend handle token expiry?

**Ideal answer.** One HTTP client module, `services/api.js`, attaches the access token to every
request and transparently refreshes on a 401 — call refresh, retry the original request, and
only surface an error if the refresh itself fails. Centralising it means no individual page
implements retry logic, and there is exactly one place to change the storage strategy when I fix
Q105.

**Why we chose this.** A single client boundary is what makes the storage decision changeable at
all. If eighty components each called `fetch` with a token, moving to httpOnly cookies would be
a rewrite.

**Alternatives.** Refresh proactively on a timer; refresh before expiry using the `exp` claim;
let requests fail and redirect to login.

**Tradeoffs.** Reactive refresh means one request pays the extra round-trip after expiry.
Proactive refresh avoids that but wakes the backend on a timer, which on a free instance that
sleeps is actively counterproductive. The classic bug in reactive refresh is the thundering
herd: several concurrent 401s each trigger their own refresh. The fix is a shared in-flight
promise, and it is worth verifying that is in place.

**Follow-ups.** "What if several requests 401 at once?" · "Do you refresh proactively?" ·
"What happens when refresh fails?"

**What interviewers expect.** The concurrent-401 problem raised unprompted. It is the standard
defect in hand-rolled refresh logic and naming it shows you have thought past the happy path.

---

### Q107. Why does the refresh endpoint have its own throttle bucket?

**Ideal answer.** Because refresh and login are different risk surfaces with different traffic
profiles. Login presents guessable credentials, so it is the credential-stuffing target and gets
5 per minute. Refresh presents an existing signed token — not guessable — so it is not a
brute-force surface, and it gets 30 per minute. The decisive reason for separating them is
shared NAT: behind one campus IP, routine hourly refresh traffic from many users would consume a
shared bucket and **starve sign-ins**. Ordinary usage would lock people out.

**Why we chose this.** It is recorded in the architecture amendment log as an M1 review finding,
which is where the shared-NAT reasoning came from.

**Alternatives.** One bucket for all auth; no limit on refresh; per-user limits on refresh.

**Tradeoffs.** Two buckets is more configuration and more to reason about. Per-user throttling on
refresh would be more precise — the token identifies the user — but a user with a valid token is
not the threat model refresh limits exist for.

**Follow-ups.** "Why is refresh not a brute-force target?" · "What is the NAT problem?" ·
"Could you throttle refresh per user?"

**What interviewers expect.** The NAT reasoning, because it is the non-obvious operational
consequence and it generalises: **per-IP limits punish shared infrastructure**, and any per-IP
control needs a story for what happens behind a corporate or campus NAT. It is also the honest
cost of the current rate limits generally, which I flag in Q120.

---

### Q108. Can you invalidate a token before it expires?

**Ideal answer.** No, not today. There is no blacklist, no token version claim, and no
server-side state to check against. If a token is stolen it is valid for up to its remaining
lifetime. The partial mitigation is the user lookup on every request — deactivating an account
takes effect immediately because `is_active` is checked from the database, even though the token
itself remains cryptographically valid.

**Why we chose this.** Statelessness was the point; a blacklist reintroduces the lookup that
statelessness avoids.

**Alternatives.** SimpleJWT's blacklist app; a `token_version` integer on the user checked per
request; short access tokens with rotating refresh; a Redis denylist keyed on `jti`.

**Tradeoffs.** A `token_version` claim is the cheapest real revocation: include it in the token,
compare it against the user row you are *already fetching*, and bump it to invalidate every
token for that user. Since the user lookup happens anyway, revocation would be nearly free —
which makes its absence harder to defend than it first appears.

**Follow-ups.** "What would you add first?" · "How much would `token_version` cost?" · "Does
deactivation work?"

**What interviewers expect.** Exactly that observation — that the usual objection to revocation
(it costs a lookup) does not apply here, because the lookup is already happening. Recognising
that your own architecture has already paid for a feature you did not implement is a sharp
piece of self-analysis.

---

### Q109. What claims are in your tokens?

**Ideal answer.** SimpleJWT defaults: `token_type`, `exp`, `iat`, `jti`, and `user_id`. No roles,
no permissions, no email. Authorisation is resolved from the database on every request rather
than carried in the token, which means a role change takes effect immediately instead of at
token expiry.

**Why we chose this.** Minimal claims mean minimal staleness. Since the user lookup happens
regardless, there is nothing to gain from duplicating authorisation data into the token.

**Alternatives.** Embed roles and permissions; embed a display name to save a lookup; embed
tenant identifiers.

**Tradeoffs.** Embedding claims removes lookups in a distributed system where each service would
otherwise need database access — that is the real argument for fat tokens, and it does not apply
to a single service. Fat tokens are also larger on every request and can leak information, since
a JWT is signed but **not encrypted**.

**Follow-ups.** "Is a JWT encrypted?" · "What is `jti` for?" · "Would you add roles?"

**What interviewers expect.** That signed ≠ encrypted — anyone can base64-decode a JWT and read
the payload, so no secret belongs in it. And `jti` is the unique token identifier that a
blacklist implementation would key on, which connects back to Q108.

---

### Q110. How do you test authentication?

**Ideal answer.** Behaviourally, at several levels. Twenty-six tests in the password-hashing
suite alone: hasher configuration and parameter pinning, legacy PBKDF2 accounts still verifying,
transparent rehash actually persisting to the database, new accounts landing as Argon2, Google
SSO accounts with unusable passwords being untouched, admin login surviving the swap, disabled
accounts, and rollback safety in both directions. Plus throttle tests asserting the boundary —
allow N, reject N+1 — and cache-health tests proving the throttle is inert when the cache does
not persist.

**Why we chose this.** Authentication failures are silent and total. Nothing else in the system
justifies this test density.

**Alternatives.** Test the happy path only; rely on manual QA; integration tests against a
staging environment.

**Tradeoffs.** Twenty-six tests for one subsystem is heavy and slows the suite. Justified because
the failure mode is "every user locked out with no error message."

**Follow-ups.** "What is your most valuable auth test?" · "How do you test rollback?" · "Did
tests catch the throttle bug?"

**What interviewers expect.** The last one answered honestly: **no.** A test asserting the 11th
login returns 429 existed and passed the whole time throttling was inert in production, because
tests use LocMemCache, which works. That is the sharpest lesson in the project — a passing test
for a security control that was doing nothing — and the response was a boot probe plus a test
that pins the *causal link* between cache persistence and throttle enforcement.

---

### Q111. How do you pin throttle limits in tests without hardcoding them?

**Ideal answer.** The tests derive the limit from `settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`
rather than hardcoding a number, so they assert the *behaviour* — allow N, reject N+1 — at
whatever N is configured. That came from a real breakage: the tests hardcoded 10, and when I
lowered `auth` to 5 for capacity reasons, they failed for the wrong reason. Deriving the value
means a rate change updates the tests automatically while still proving the boundary holds.

**Why we chose this.** A test that breaks when configuration changes legitimately is a test that
gets edited carelessly.

**Alternatives.** Hardcode and update; `override_settings` with a fixed rate; skip boundary
assertions.

**Tradeoffs.** Deriving from settings means the test cannot catch a *wrong* configured value —
if someone sets `auth` to 5000, the test still passes. That is why there is a separate test
asserting the ceiling stays within measured capacity, which is the one that catches a bad value.

**Follow-ups.** "So what catches a bad limit?" · "Is that test mutation-tested?" · "Why not
`override_settings`?"

**What interviewers expect.** The two-test split: one pins the *mechanism* (the brake engages at
the configured point), the other pins the *policy* (the configured point is below measured
capacity). And that the second is mutation-verified — restoring the old value fails it with
`assert 35 <= 20`. Separating mechanism from policy in tests is a genuinely good instinct.

---

### Q112. What is the biggest weakness in your auth system?

**Ideal answer.** Token storage in `localStorage`, because it turns any XSS into full account
takeover with an hour of persistence and no revocation path. Second is that `SIGNING_KEY` is
`SECRET_KEY`, so JWT signing shares a key with sessions and reset tokens and cannot be rotated
independently. Third is the absence of revocation, which as I noted is almost free to add given
the per-request user lookup already happens. All three are known, documented, and unfixed — the
first is in my architecture document's divergence list.

**Why we chose this.** Reflection.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "Why have you not fixed the storage?" · "Which would you fix first?" · "How long
would each take?"

**What interviewers expect.** An ordered plan with effort estimates, not just a list.
`token_version` revocation is roughly an afternoon and uses a lookup you already pay for. A
separate `SIGNING_KEY` is a config change plus a forced logout. httpOnly cookies are the largest
because they touch the deployment's cross-origin setup and the frontend refresh flow. Ordering
by value-per-effort — revocation, then key split, then storage — is the answer.

---

## Section J — OAuth & Federated Identity (Q113–Q118)

---

### Q113. Walk me through your Google Sign-In flow.

**Ideal answer.** Google Identity Services runs in the browser and produces a signed ID token.
The frontend POSTs it to `/api/auth/google/`. The backend verifies the token's signature and
**audience** directly against Google's public keys using `google.oauth2.id_token.verify_oauth2_token`
with `GOOGLE_CLIENT_ID` as the expected audience — the frontend is never trusted to assert who
the user is. It then checks `email_verified`, get-or-creates a local account keyed on email, and
issues our own JWT pair. From that point on the rest of the application needs no Google
awareness at all: every `IsAuthenticated` view sees an ordinary user.

**Why we chose this.** Translating the federated identity into a first-party token at the
boundary means Google is an authentication *source*, not a dependency of every request.

**Alternatives.** Full OAuth authorization-code flow with a redirect; django-allauth; passing the
Google token to every endpoint.

**Tradeoffs.** The ID-token flow is simpler than authorization-code and appropriate when you only
need identity, not API access on the user's behalf. If I ever needed to read their Google Drive,
I would need the code flow and refresh tokens.

**Follow-ups.** "Why verify the audience?" · "What is the difference from the code flow?" ·
"What if the token is replayed?"

**What interviewers expect.** The audience check explained as a *real* attack, not a checkbox:
without it, a token minted for a completely different application — any app the user has signed
into — would be accepted here. Audience is what binds the token to your application.

---

### Q114. Why is email the join key?

**Ideal answer.** If a Google sign-in's email matches an existing local account, that account is
reused as-is, so a user who registered with a password can later sign in with Google and reach
the same account. The alternative — creating a second account — produces duplicate identities,
split submission history, and a support ticket. It relies on `email_verified` being true, which
is checked explicitly and rejected with a 401 otherwise.

**Why we chose this.** Account fragmentation is a worse user experience than the linking risk,
provided the email is verified.

**Alternatives.** Separate identity table with explicit account linking; refuse to auto-link and
require an in-app confirmation; use Google's `sub` claim as the key.

**Tradeoffs.** Auto-linking on email is a known account-takeover vector **if** the provider does
not verify emails — an attacker registers your email at a sloppy provider and takes over your
account. It is safe with Google because `email_verified` is meaningful there, and unsafe as a
general pattern. Using the `sub` claim would be immune but would not link to pre-existing local
accounts, which was the point.

**Follow-ups.** "Is that not an account-takeover risk?" · "What if `email_verified` is false?" ·
"What about a second provider?"

**What interviewers expect.** Naming the takeover vector and explaining why it does not apply
*here* while conceding it would with a second, less trustworthy provider. Adding a provider would
require rethinking this, and saying so is better than defending it as universally safe.

---

### Q115. Why `set_unusable_password()` rather than a random password?

**Ideal answer.** Google-only accounts have no local secret at all. Django stores a `!` sentinel
that no hasher can match, so `check_password` always returns `False` — there is nothing to guess,
leak, or brute-force. A random password would be a real credential sitting in the database
serving no purpose, and it would make the password-reset flow able to produce a working local
login for an account that was never meant to have one. Six accounts in production are in this
state, and there is a test asserting they cannot be logged into with the placeholder value.

**Why we chose this.** The smallest attack surface is the one that does not exist.

**Alternatives.** Random password; a null password field; a separate flag column.

**Tradeoffs.** Users cannot fall back to password login if Google is unavailable. That is the
correct tradeoff — the account genuinely has no password — but it does mean Google outage equals
sign-in outage for those users, and a "set a password" flow would be the remedy.

**Follow-ups.** "What if Google is down?" · "Can they add a password later?" · "How is unusable
represented?"

**What interviewers expect.** That the migration correctly leaves these accounts alone — there
is a test named `test_unusable_password_is_untouched_by_the_migration`, and the status command
excludes them from the denominator so the migration can actually reach 100%. Edge cases that
break progress reporting are exactly what gets missed.

---

### Q116. Why does the Google endpoint return 503 when unconfigured?

**Ideal answer.** If `GOOGLE_CLIENT_ID` is unset, there is no audience to verify against — so
the choice is between failing loudly and silently accepting tokens with no audience check. It
returns 503 and logs an error, because a misconfiguration must not look like success. There is a
test named `test_unconfigured_server_fails_loudly_not_silently` pinning exactly that.

**Why we chose this.** The failure being prevented is severe and quiet: accepting any Google
token from any application as valid identity here.

**Alternatives.** Return 401; disable the route entirely when unconfigured; fall back to
`GOOGLE_CLIENT_ID = None` and let the library decide.

**Tradeoffs.** 503 says "the server is misconfigured," which is arguably information disclosure —
it tells a probe that SSO exists but is not set up. That is a trivial leak against a
configuration error the operator needs to see.

**Follow-ups.** "Why not 401?" · "Is that information disclosure?" · "How would you notice in
production?"

**What interviewers expect.** The general principle stated: **a security control that is absent
must fail closed and visibly.** The library would have accepted `None` as an audience in some
configurations, which is the silent-acceptance case, and that is why the guard is explicit rather
than delegated.

---

### Q117. How do you generate usernames from Google emails?

**Ideal answer.** `_unique_username_from_email` takes the local part, strips anything Django's
`UnicodeUsernameValidator` would reject with `[^\w.@+-]`, lowercases, truncates to 140 characters
to leave room for a numeric suffix under the 150-character field limit, then loops appending a
counter until the username is unique with a case-insensitive check.

**Why we chose this.** An email local part can legally contain characters a Django username
cannot — quoted strings, unusual punctuation — so passing it through unsanitised would produce
validation errors at `create()` for a subset of users.

**Alternatives.** Use the full email as the username; a UUID; ask the user to pick one.

**Tradeoffs.** The uniqueness loop is a read-then-write race: two simultaneous sign-ups from
similar emails could both find the same candidate free. In practice it is inside a transaction
and the username field is unique, so the loser gets an `IntegrityError` rather than a duplicate —
a crash rather than corruption, which is the acceptable failure ordering but not a graceful one.

**Follow-ups.** "Is that loop a race?" · "Why 140 characters?" · "Why case-insensitive?"

**What interviewers expect.** Spotting your own race unprompted, and knowing the consequence is
bounded by the database constraint. The 140-character detail is the kind of thing that only
appears when someone has actually hit a field-length error.

---

### Q118. Why does Google Sign-In share the login throttle bucket?

**Ideal answer.** Because it is just another way to obtain a token pair. If it had its own bucket
— or none — it would be an unthrottled side-door around the credential-stuffing brake. It uses
`ClientIPScopedRateThrottle` with `throttle_scope = 'auth'`, the same 5-per-minute bucket as
password login, and there is a test named
`test_google_login_is_rate_limited_same_scope_as_password_login` asserting the boundary.

**Why we chose this.** Rate limits should be attached to the *capability* — obtaining a token —
not to the endpoint that provides it.

**Alternatives.** Separate scope for SSO; no throttle since tokens are Google-signed; per-user
limits.

**Tradeoffs.** SSO is arguably less brute-forceable than password login, since forging a
Google-signed token is infeasible. But the endpoint still costs a network round-trip to Google
plus a database write, so the throttle is doing capacity work even where it is not doing
brute-force work.

**Follow-ups.** "But you cannot brute-force a signed token." · "So is the throttle pointless?" ·
"What is it protecting?"

**What interviewers expect.** The reframe when challenged: on this deployment the throttle is
**also** a capacity control, and this endpoint makes an outbound HTTPS call to Google on a
serialised worker. Unlimited SSO attempts would be a denial-of-service vector regardless of
whether they can succeed. Holding your position under pushback, with a different justification
than the one challenged, is a good sign.

---

## Section K — Application Security & Threat Model (Q119–Q126)

---

### Q119. What is your threat model?

**Ideal answer.** Four boundaries, each with its own enforcement. Untrusted HTTP clients are
handled by JWT authentication, DRF permissions, and per-IP throttles. Student-submitted source
code is the most interesting one — it is **never executed in-process**; it is string-templated
into a wrapper and shipped to Judge0's sandbox, so there is no `eval`, no `exec`, no subprocess,
and a sandbox escape is Judge0's problem rather than mine. LLM output is semi-trusted: every
generation path validates structure and filters placeholders, so a model returning prose instead
of JSON produces a retry rather than a crash. And Judge0's responses are mapped by status code,
never evaluated.

**Why we chose this.** The dominant risk in a coding-practice platform is arbitrary code
execution, and the only robust answer is to not execute it yourself.

**Alternatives.** Self-hosted Judge0 in an isolated fleet (the architecture's later phase);
containers per submission; gVisor or Firecracker; static analysis and refuse to run.

**Tradeoffs.** Outsourcing execution means trusting a third party with student code and
depending on their availability — which is exactly why grading has no fallback and returns 503.
Self-hosting would remove the dependency and add a substantial isolation problem I am not
currently equipped to solve well.

**Follow-ups.** "What if Judge0 is compromised?" · "What does the LLM validation catch?" ·
"Would you self-host?"

**What interviewers expect.** "Never execute untrusted code in your own process" stated as an
absolute, and the observation that the wrapper layer is a *templating* boundary, not a security
boundary — it exists for correctness, and pretending it sanitises anything would be dangerous.

---

### Q120. Your rate limits are spoofable. Defend that.

**Ideal answer.** They key on the first `X-Forwarded-For` hop, which is client-supplied, so an
attacker rotating that header gets a fresh bucket each time. That is a genuine weakening versus
`NUM_PROXIES=1`, and I chose it deliberately, because the prior behaviour required **no evasion
effort at all** — Render's load balancer rotates internal IPs, so twelve sequential requests
landed in three different buckets and no limit was ever reached. A spoofable limit strictly
dominates an absent one. The tradeoff is documented and there is a test asserting the evasion
works, so nobody removes the limitation silently or worsens it accidentally.

**Why we chose this.** Between a control that stops nothing and a control that stops
unsophisticated attacks, the second is better, and pretending otherwise would be worse than
either.

**Alternatives.** Correct `NUM_PROXIES` for the topology (Render's hop count is not stable);
edge rate limiting at Cloudflare; authenticated-only limits; proof-of-work.

**Tradeoffs.** Edge limiting is the right long-term answer — it rejects before consuming a worker
thread and sees the real client IP. It requires a CDN in front of the API, which the architecture
diagram already anticipates and the deployment does not yet have.

**Follow-ups.** "So a determined attacker gets through?" · "Why test a weakness?" · "What is the
real fix?"

**What interviewers expect.** Two things. The test-the-weakness instinct, which usually surprises
people: pinning a limitation means it cannot be silently changed in either direction. And an
honest statement that a determined attacker *does* get through — the control stops credential
stuffing from a naive script, not a competent adversary, and the real fix is at the edge.

---

### Q121. How do you handle CORS?

**Ideal answer.** `django-cors-headers` with `CORS_ALLOWED_ORIGINS` from an environment variable,
defaulting to localhost origins for development. `CORS_ALLOW_CREDENTIALS = True` and, notably,
`CORS_ALLOW_ALL_ORIGINS = DEBUG` — so wildcard origins are enabled only in development and the
production deployment uses an explicit allowlist driven by `CORS_ALLOWED_ORIGINS`. The
`CorsMiddleware` is first in the chain so preflight responses carry the right headers even when
something downstream short-circuits.

**Why we chose this.** Tying the wildcard to `DEBUG` means the permissive setting cannot survive
into production without also turning debug on, which the secret-key guard already blocks.

**Alternatives.** Static allowlist with no env override; regex origin matching; no CORS by
serving the SPA from the same origin.

**Tradeoffs.** Same-origin hosting would remove CORS entirely and is arguably the cleanest
answer — it is what a reverse proxy in front of both would give you. The Vercel/Render split
buys free CDN hosting and costs a cross-origin configuration.

**Follow-ups.** "What does CORS actually protect?" · "Is `ALLOW_ALL_ORIGINS` in dev risky?" ·
"Why is the middleware first?"

**What interviewers expect.** Correctness about what CORS is: a **browser** protection that
governs whether script on origin A may *read* a response from origin B. It is not server-side
access control — `curl` ignores it entirely — so CORS is never a substitute for authentication.
Candidates routinely describe it as if it stops requests, and it does not.

---

### Q122. Do you need CSRF protection with JWTs?

**Ideal answer.** For the API, no — CSRF exploits *ambient* credentials, cookies the browser
attaches automatically. A Bearer token must be added by JavaScript, so a cross-site form post
cannot carry it. `CsrfViewMiddleware` remains in the chain because Django Admin uses session
cookies and genuinely needs it, and `CSRF_TRUSTED_ORIGINS` is configured from the environment for
that path. In production `CSRF_COOKIE_SECURE` and `SESSION_COOKIE_SECURE` are both set, along
with `SECURE_PROXY_SSL_HEADER` so Django knows the proxy terminated TLS.

**Why we chose this.** The API and the admin have different session models, so they need
different protections, and both live in one process.

**Alternatives.** Remove CSRF middleware entirely; use cookies for the API and require CSRF
tokens; separate the admin onto its own deployment.

**Tradeoffs.** If I move the refresh token to an httpOnly cookie as the architecture specifies,
CSRF becomes relevant to the API too — the refresh endpoint would then have an ambient
credential. That is a real coupling between the two fixes and worth stating.

**Follow-ups.** "What if you switch to cookies?" · "Why is the middleware still there?" · "What
does `SECURE_PROXY_SSL_HEADER` do?"

**What interviewers expect.** The precise reason JWTs are CSRF-immune — non-ambient credentials —
rather than "JWTs do not need CSRF." And the forward-looking point that the httpOnly-cookie fix
would reintroduce the requirement, which shows you are reasoning about the system rather than
reciting a rule.

---

### Q123. How do you prevent SQL injection?

**Ideal answer.** The ORM parameterises everything, and there is essentially no raw SQL in
application code — the exceptions are `SELECT 1` in the health check and DDL in the partition
maintenance command, neither of which takes user input. Where dynamic filtering happens, it goes
through the ORM's query API with values bound as parameters rather than interpolated.

**Why we chose this.** Parameterisation by default is the strongest available protection, and
using the ORM idiomatically gets it for free.

**Alternatives.** Raw SQL with explicit parameters; a query builder; stored procedures.

**Tradeoffs.** ORM-only means some queries are harder to express or less efficient than
hand-written SQL. The recommender's Elo-band selection uses `Func` and `F` expressions to compute
`ABS(base_difficulty - target_elo)` in the database, which is more awkward than raw SQL but keeps
parameterisation automatic.

**Follow-ups.** "Where do you use raw SQL?" · "Is `extra()` safe?" · "What about `raw()` with
parameters?"

**What interviewers expect.** Knowing the dangerous ORM escape hatches by name: `.extra()`,
`.raw()`, and `RawSQL` all accept string fragments, and `.extra()` in particular is deprecated
largely because it invites interpolation. Being able to say "these are the three places the ORM
stops protecting you" is the answer that shows real familiarity.

---

### Q124. What about XSS?

**Ideal answer.** The API returns JSON, not HTML, so server-side template injection is not the
vector. The real exposure is the React frontend rendering user-generated content — chat messages,
group names, profile fields — and React escapes by default, so the risk concentrates on any
`dangerouslySetInnerHTML` and on rendering LLM output. That last one is worth calling out: LLM
responses are displayed to users, and a model can be prompted to emit markup, so anywhere the
tutor or RAG answer is rendered needs to be escaped or sanitised rather than trusted.

**Why we chose this.** React's default escaping does most of the work; the residual risk is
wherever that default is bypassed.

**Alternatives.** Content Security Policy; server-side sanitisation with bleach; markdown
rendering with an allowlist.

**Tradeoffs.** A strict CSP is the highest-value addition and I do not have one. It would turn a
successful XSS into a mostly-inert one by blocking inline script and restricting connect-src —
and it is a header, not a refactor.

**Follow-ups.** "Do you have a CSP?" · "What about markdown in chat?" · "Why does XSS matter more
here?"

**What interviewers expect.** The connection back to Q105: XSS matters more in SparkLM
*specifically because* tokens are in `localStorage`, so a successful XSS is a full account
takeover rather than a session-scoped nuisance. Linking two weaknesses into a combined severity
is exactly the reasoning a security-minded interviewer is testing for.

---

### Q125. What is your biggest unmitigated security risk?

**Ideal answer.** The combination of `localStorage` token storage with no Content Security
Policy — either alone is manageable, together they mean one XSS equals full account takeover
with an hour of persistence and no revocation path. After that, the spoofable rate limits, which
mean credential stuffing is slowed rather than stopped. Third, no continuous dependency
scanning, so a vulnerable transitive dependency could sit unnoticed. All three are documented,
and none is currently mitigated.

**Why we chose this.** Compounding risks are the ones worth ranking first, and they are easy to
miss when you assess each control in isolation.

**Alternatives.** N/A.

**Tradeoffs.** N/A.

**Follow-ups.** "Which would you fix first?" · "How long would a CSP take?" · "How would you
prioritise?"

**What interviewers expect.** Prioritisation by value per effort, not by severity alone. A CSP
header is hours of work and blunts the worst compound risk immediately, so it goes first.
`pip-audit` in CI is one file. httpOnly cookies are the biggest change for the largest structural
win. Ranking by *what you can ship this week* is the practical engineering answer.

---

### Q126. If you had one week for security, what would you do?

**Ideal answer.** Day one, a Content Security Policy and `pip-audit` in CI — both cheap, both
close real gaps. Day two, `token_version` revocation, which is almost free because the per-request
user lookup already happens. Day three, split `SIGNING_KEY` from `SECRET_KEY` so JWT signing can
rotate independently. Days four and five, move the refresh token to an httpOnly cookie and the
access token in memory, which is the architecture's specified design and the largest change.
Then secret scanning in pre-commit, and the `is_active` and enumeration tests I would want
alongside all of it.

**Why we chose this.** Ordered by value per effort, front-loading the changes that are
configuration rather than refactoring.

**Alternatives.** Start with a penetration test; start with the biggest structural change; hire
an audit.

**Tradeoffs.** A pentest would find things I have not thought of, which is exactly its value —
but with a known list of unmitigated risks, spending the week fixing them beats spending it
discovering more.

**Follow-ups.** "Why not a pentest first?" · "What would you not do?" · "What is missing from
that list?"

**What interviewers expect.** A defensible ordering and the discipline to say what you would
*not* do — I would not add WAF rules, tighten rate limits further, or add 2FA, because none of
those addresses a risk I have actually identified, and security theatre on a known-vulnerable
system is worse than nothing. Knowing what to skip is as informative as knowing what to do.

---

## Part 3 Recap — Five More Stories

| # | Story | The one-line hook |
|---|---|---|
| 11 | **Django's defaults would have OOM'd us** | 100 MiB × 4 concurrent logins against 512 MB, and the OOM cost is a 93-second cold start for everyone after. |
| 12 | **Rollback is a reorder** | Removing the Argon2 hasher locks out every migrated user, and Django reports it as an ordinary wrong password. |
| 13 | **The security improvement that added a leak** | Before the migration every login path was PBKDF2-dominated and indistinguishable; afterwards, response time reveals account state. |
| 14 | **The passing test that proved nothing** | A test asserting the 11th login returns 429 passed for the entire period throttling was inert in production. |
| 15 | **Testing my own weakness** | There is a test asserting XFF rotation *evades* the throttle — so the limitation cannot be silently removed or silently worsened. |

Part 1 was silent failures. Part 2 was wrong instruments. Part 3 is **the cost of every
control** — the migration that improved hashing and added a timing channel, the throttle fix that
traded unspoofable-but-useless for spoofable-but-working, the statelessness that bought scaling
and sold revocation. The through-line to state in an interview: *security decisions are trades,
and the ones I can defend are the ones where I can name what I gave up.*

---

*End of Part 3 (Questions 85–126). Part 4 — Performance, Scalability & Deployment — follows.*
