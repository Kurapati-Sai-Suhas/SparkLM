"""
common.languages — the single source of truth for programming languages
(M4 Phase B).

Language identity used to be a bare string repeated in four places that
drifted apart, and that drift caused three separate production bugs:

  1. `LANGUAGE_IDS` held "js" but not "javascript", while the serializer
     accepted "javascript" — so every JavaScript submission failed with
     "Unsupported language".
  2. `hidden_wrapper_code` keys disagreed across seed generations, which is
     why `WRAPPER_LANGUAGE_ALIASES` exists at all.
  3. Found while writing this module: `ALLOWED_LANGUAGES` omitted **"c"**,
     so the serializer rejected every C submission — even though Judge0 has
     an id for it, `reseed_questions` generates C stubs, and the frontend
     offers C with a self-contained main() skeleton. A student could pick C,
     write a solution, and be told the language is unsupported.

Three symptoms of one cause is a pattern, not bad luck. Everything that
needs to know about a language now derives from `REGISTRY`, so adding one is
a single edit and the maps cannot disagree.

Deliberately plain data — no Django import, no ORM, no settings. It is
imported by the serializer, the Judge0 runner and the wrapper resolver, and
a dependency in the other direction would create an import cycle.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    """One supported language and everything the platform needs about it."""

    key: str            # canonical internal name
    label: str          # display name for the UI
    judge0_id: int      # Judge0 language_id
    extension: str      # file extension, for downloads
    aliases: tuple      # other accepted spellings (must be unique globally)
    self_contained: bool = False   # True => student writes a whole program
    # Spelling the SPA uses as its selector value and boilerplate key, when
    # it differs from `key`. Only JavaScript does: the backend canonicalises
    # to "javascript" while the UI has always used "js". Recorded here so the
    # difference lives in one place instead of being rediscovered on both
    # sides — it is the mismatch that gave every JavaScript user an empty
    # editor. None means "same as key".
    ui_key: str = None

    @property
    def spellings(self):
        """Canonical key first, then aliases — the wrapper lookup order."""
        return (self.key,) + self.aliases

    @property
    def frontend_key(self):
        return self.ui_key or self.key


# Order is display order in the language selector.
REGISTRY = (
    Language("python", "Python", 71, "py", ()),
    Language("java", "Java", 62, "java", ()),
    Language("cpp", "C++", 54, "cpp", ("c++",), self_contained=True),
    # self_contained: C and C++ have no generic wrapper, so the student
    # writes a complete program including main(). Python/Java/JS use runtime
    # reflection over a Solution class instead.
    Language("c", "C", 50, "c", (), self_contained=True),
    Language("javascript", "JavaScript", 63, "js", ("js",), ui_key="js"),
)


def export_payload():
    """
    The registry as plain data, for generating the frontend's copy.

    Consumed by `manage.py export_languages`, which writes
    `studysphere-ai-11/src/lib/languages.generated.json`. Keeping the shaping
    here rather than in the command means the contract lives next to the data
    it describes.
    """
    return {
        "_comment": (
            "GENERATED from backend/LearnLM/common/languages.py by "
            "`manage.py export_languages`. Do not edit by hand — CI "
            "regenerates and fails on drift."
        ),
        "languages": [
            {
                "key": lang.frontend_key,
                "backendKey": lang.key,
                "label": lang.label,
                "extension": lang.extension,
                "spellings": list(lang.spellings),
                "selfContained": lang.self_contained,
            }
            for lang in REGISTRY
        ],
    }

_BY_SPELLING = {
    spelling: lang for lang in REGISTRY for spelling in lang.spellings
}

# Guard against a typo silently shadowing a language: two entries claiming
# the same spelling would make one unreachable, which is precisely the class
# of bug this module exists to prevent.
_expected = sum(len(lang.spellings) for lang in REGISTRY)
if len(_BY_SPELLING) != _expected:
    raise RuntimeError(
        "Duplicate language spelling in common.languages.REGISTRY — "
        "one language is shadowing another."
    )


def get(name):
    """Resolve any accepted spelling to its Language, or None."""
    if not name:
        return None
    return _BY_SPELLING.get(str(name).strip().lower())


def is_supported(name):
    return get(name) is not None


def canonical(name):
    """Canonical key for any accepted spelling, or None."""
    lang = get(name)
    return lang.key if lang else None


def judge0_id(name):
    lang = get(name)
    return lang.judge0_id if lang else None


def wrapper_spellings(name):
    """
    Keys to try when looking a language up in `Question.hidden_wrapper_code`,
    most-canonical first. Seed data is inconsistent across generations, so a
    template may be filed under any accepted spelling.
    """
    lang = get(name)
    return lang.spellings if lang else ((str(name).lower(),) if name else ())


def is_self_contained(name):
    lang = get(name)
    return bool(lang and lang.self_contained)


# ── Derived views, so existing call sites keep their shape ────────────────
# These replace the hand-maintained dicts that drifted. They are built from
# REGISTRY, so they cannot disagree with it or with each other.

ACCEPTED_SPELLINGS = frozenset(_BY_SPELLING)
CANONICAL_KEYS = tuple(lang.key for lang in REGISTRY)
LANGUAGE_IDS = {spelling: lang.judge0_id for spelling, lang in _BY_SPELLING.items()}
SELF_CONTAINED = frozenset(lang.key for lang in REGISTRY if lang.self_contained)
