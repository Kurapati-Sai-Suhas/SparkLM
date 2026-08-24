"""
Offline knowledge-tracing dataset pipeline (M2 P2.10b).

A RESEARCH ARTIFACT, deliberately outside the Django application. The core
modules import no Django at all; only `adapters.learnlm` touches the ORM, and
it imports lazily. Nothing here is reachable from a request, and nothing here
writes application state.

    schema      canonical interaction record + provenance
    validation  row validation with machine-readable rejection reasons
    duplicates  the deterministic duplicate policy
    sources     dataset-specific readers (real schemas; no bundled data)
    pipeline    deterministic build -> split -> manifest
    adapters    LearnLM trust firewall

── Why the web tier never learns about this ───────────────────────────────

`requirements.txt:53` keeps the web tier torch-free and Render runs it on a
512 MB free instance. A benchmark dataset that the application could import is
a benchmark dataset that will eventually be imported at request time. Keeping
the package Django-free by construction makes that impossible rather than
discouraged.
"""
