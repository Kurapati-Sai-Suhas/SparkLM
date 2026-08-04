"""
Configuration register contract (M4 Phase C).

`docs/FEATURE_FLAGS.md` documents every environment variable the backend reads.
Without a test it would be documentation-by-convention, and this codebase has
been bitten three times by configuration that was present, plausible and inert
(dead password validators, an inert throttle, a non-persisting cache). A
register that silently falls out of date is the same failure one level up.

So the register is enforced in both directions:

  * a variable read in code but missing from the register fails the build —
    undocumented configuration cannot ship;
  * a variable listed in the register but read nowhere fails too — otherwise
    the register accumulates ghosts and stops being trustworthy.

Deliberately a static scan, not an import-time check: configuration is read at
module import across several modules, and some of it (`ENABLE_SHAP_XAI`,
`JUDGE0_URL`) is read outside `settings.py` entirely.
"""

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]          # backend/LearnLM
REGISTER = BACKEND.parents[1] / "docs" / "FEATURE_FLAGS.md"

# os.getenv("X") / os.environ.get("X"), single or double quoted.
ENV_READ = re.compile(r'(?:os\.getenv|os\.environ\.get)\(\s*["\']([A-Z][A-Z0-9_]*)["\']')

# Variables read only by tooling that never runs in the deployed service.
# Kept explicit rather than pattern-matched so adding one is a visible choice.
EXEMPT = {
    "PYTHONIOENCODING",   # harness/encoding, not application configuration
}


def _iter_source():
    for path in BACKEND.rglob("*.py"):
        p = path.as_posix()
        if "__pycache__" in p or "/migrations/" in p or "/scripts/" in p:
            continue
        if path.name.startswith("test_"):
            continue
        yield path


def env_vars_read_in_code():
    found = {}
    for path in _iter_source():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in ENV_READ.finditer(text):
            found.setdefault(match.group(1), set()).add(path.as_posix())
    return {k: v for k, v in found.items() if k not in EXEMPT}


def env_vars_in_register():
    """
    Every `BACKED_TICK` name in the register. Braced forms such as
    `POSTGRES_{DB,USER}` are expanded, because writing five near-identical
    rows for one credential group would make the document worse to read.
    """
    text = REGISTER.read_text(encoding="utf-8", errors="replace")
    names = set()
    for token in re.findall(r"`([A-Z][A-Z0-9_]*(?:\{[A-Z0-9_,]+\})?)`", text):
        brace = re.match(r"([A-Z0-9_]*)\{([A-Z0-9_,]+)\}", token)
        if brace:
            prefix, options = brace.groups()
            names.update(f"{prefix}{o}" for o in options.split(","))
        else:
            names.add(token)
    return names


def test_the_register_exists():
    assert REGISTER.exists(), f"missing {REGISTER}"


def test_every_variable_read_in_code_is_documented():
    undocumented = {
        name: sorted(paths)
        for name, paths in env_vars_read_in_code().items()
        if name not in env_vars_in_register()
    }
    assert not undocumented, (
        "environment variables read in code but absent from "
        "docs/FEATURE_FLAGS.md:\n"
        + "\n".join(f"  {n} — {p[0]}" for n, p in sorted(undocumented.items()))
        + "\n\nAdd an entry in the same commit as the code that reads it. "
        "A flag also needs an expiry date and the condition that resolves it."
    )


def test_the_register_lists_no_ghosts():
    read = set(env_vars_read_in_code())
    # Names the register mentions as prose rather than as configuration it owns.
    NARRATIVE = {
        "MEDIA_ROOT", "DEBUG", "STORAGES",           # the ephemeral-media note
        "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE", "SECURE_PROXY_SSL_HEADER",
    }
    ghosts = env_vars_in_register() - read - NARRATIVE
    assert not ghosts, (
        f"docs/FEATURE_FLAGS.md documents variables nothing reads: "
        f"{sorted(ghosts)}. Remove them, or add them to NARRATIVE if they are "
        f"referenced as prose rather than owned as configuration."
    )


@pytest.mark.parametrize("flag", ["CURRICULUM_GATE_ENFORCE", "ENABLE_SHAP_XAI"])
def test_behaviour_flags_carry_a_resolution(flag):
    """
    A flag without a stated resolution is a flag that becomes permanent by
    accident — which is precisely what happened to CURRICULUM_GATE_ENFORCE
    between July and this milestone.
    """
    text = REGISTER.read_text(encoding="utf-8", errors="replace")
    assert flag in text
    section = text.split(f"### `{flag}`", 1)
    assert len(section) == 2, f"{flag} has no resolution section in the register"
    body = section[1][:1200]
    assert re.search(r"expiry|permanent|trigger|revisit", body, re.I), (
        f"{flag}'s section states no expiry, trigger or permanence decision"
    )


def test_admission_limit_documents_its_per_process_caveat():
    """
    The limit is a threading.BoundedSemaphore, so N worker processes admit
    N x the configured value. Correct today at one process; wrong the moment
    Milestone 5 adds workers, which is when someone will read this row.
    """
    text = REGISTER.read_text(encoding="utf-8", errors="replace")
    row = next(l for l in text.splitlines() if "`ADMISSION_LIMIT`" in l)
    assert "per-process" in row.lower()
