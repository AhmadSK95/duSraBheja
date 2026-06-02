"""Diceware passphrase generator.

Produces dash-separated passphrases for vault setup. The dashboard form
can call ``generate_passphrase()`` server-side to suggest a strong default
without the owner having to come up with one cold.

Current state: ships with a ~200-word builtin list. 8 words from this list
gives ~61 bits of entropy, comfortably above the 50-bit floor we accept
for a vault passphrase.

Upgrade path: drop the EFF Large wordlist (7,776 words) at
``src/api/static/diceware/eff_large.txt`` and set
``settings.diceware_wordlist_path``. ``generate_passphrase()`` reads from
the file when set, falls back to the builtin. 8 words from EFF Large
gives ~103 bits — what you actually want long-term, deferred to a small
follow-up commit so this PR can ship the rest of the setup flow.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

# ── Builtin wordlist ──────────────────────────────────────────────────────
# Short, common, memorable English words. Curated to avoid homophones and
# spellings that are easy to mistype. Not exhaustive; replace with the EFF
# Large list when bundling that becomes a priority.
_BUILTIN_WORDS: tuple[str, ...] = (
    "acorn", "anchor", "angel", "ankle", "apple", "armor", "arrow", "atlas",
    "axis", "azure", "badge", "baker", "basil", "basin", "basket", "batch",
    "beacon", "beard", "beetle", "berry", "bicycle", "billow", "birch", "bishop",
    "blanket", "blossom", "boiler", "booklet", "border", "boulder", "branch", "brave",
    "breeze", "brick", "bridge", "bronze", "brook", "bubble", "buckle", "buffalo",
    "buffer", "bullet", "bumper", "bunker", "butler", "button", "cabin", "cactus",
    "camera", "candle", "candy", "canyon", "captain", "carbon", "cargo", "carpet",
    "castle", "cattle", "celery", "cement", "center", "ceramic", "cereal", "chalk",
    "channel", "chapel", "charm", "cheese", "cherry", "chess", "chimney", "cinema",
    "circle", "classic", "clever", "climate", "cluster", "coast", "cobra", "coconut",
    "coffee", "comet", "compass", "copper", "coral", "corner", "cotton", "couple",
    "coyote", "cradle", "crater", "crayon", "creek", "crimson", "crown", "cube",
    "cucumber", "custom", "cyclone", "dagger", "dairy", "dance", "danger", "debate",
    "decoy", "delta", "demon", "denim", "depth", "derby", "desert", "detail",
    "diamond", "diary", "dimple", "dinner", "diploma", "direct", "disco", "doctor",
    "dolphin", "domain", "doodle", "double", "dragon", "dream", "drift", "drum",
    "eagle", "earth", "easel", "eclipse", "echo", "ember", "emerald", "empire",
    "energy", "engine", "entry", "equator", "escape", "essay", "ethic", "event",
    "evidence", "exam", "exit", "expert", "fable", "fabric", "factor", "fade",
    "faith", "falcon", "family", "famous", "farmer", "faucet", "fence", "festival",
    "field", "finch", "finger", "fire", "fitness", "flag", "flame", "flask",
    "flock", "flora", "flour", "flower", "flute", "focus", "foggy", "force",
    "forest", "formal", "fossil", "foster", "fountain", "fragrant", "frame", "freezer",
    "friend", "frost", "fudge", "fuel", "future", "galaxy", "gallon", "garage",
    "garden", "garlic", "gateway", "gather", "gecko", "ghost", "ginger", "glacier",
    "glare", "glass", "glider", "glitter", "globe", "glory", "glove", "goblin",
)


# ── Public API ────────────────────────────────────────────────────────────


@lru_cache(maxsize=4)
def _load_wordlist(path_str: str | None) -> tuple[str, ...]:
    """Load wordlist from disk if path given, else return builtin. Cached so
    repeated calls don't re-read the file.
    """
    if not path_str:
        return _BUILTIN_WORDS
    p = Path(path_str)
    if not p.exists():
        return _BUILTIN_WORDS
    words = tuple(
        line.strip().split("\t")[-1]  # EFF format is "11111\tword" per line
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    )
    return words or _BUILTIN_WORDS


def generate_passphrase(
    *,
    word_count: int = 8,
    separator: str = "-",
    wordlist_path: str | None = None,
) -> str:
    """Return a passphrase: ``word1-word2-...`` of ``word_count`` words.

    Uses ``secrets.choice`` (CSPRNG); never the ``random`` module. Each
    word is chosen independently and uniformly.

    Strength reference (builtin 200-word list):
      - 6 words  ~ 46 bits
      - 7 words  ~ 54 bits
      - 8 words  ~ 61 bits  ← default
      - 9 words  ~ 69 bits

    With EFF Large (7,776 words):
      - 6 words  ~ 77 bits
      - 7 words  ~ 90 bits
      - 8 words  ~103 bits

    The setup route enforces ``word_count >= 6`` server-side regardless of
    what the form sends, so a tampered request can't trivially weaken
    the generated passphrase.
    """
    if word_count < 6:
        raise ValueError("Diceware passphrase must use at least 6 words")
    words = _load_wordlist(wordlist_path)
    chosen = (secrets.choice(words) for _ in range(word_count))
    return separator.join(chosen)


def estimate_entropy_bits(passphrase: str, wordlist_path: str | None = None) -> float:
    """Heuristic entropy estimate for an owner-typed passphrase.

    Two paths:
      - If the passphrase looks like diceware (≥6 dash-or-space-separated
        tokens, each present in the wordlist), score it as
        ``count * log2(wordlist_size)``.
      - Otherwise score the character composition: ``length *
        log2(charset_size)`` where charset_size adapts to which classes
        are present (lower, upper, digit, symbol).

    Not as good as zxcvbn, but dependency-free and adequate to enforce a
    sensible floor before vault setup completes.
    """
    import math

    if not passphrase:
        return 0.0

    words = _load_wordlist(wordlist_path)
    word_set = frozenset(w.lower() for w in words)
    # Diceware detection
    tokens = [t for t in passphrase.replace(" ", "-").split("-") if t]
    if len(tokens) >= 6 and all(t.lower() in word_set for t in tokens):
        return len(tokens) * math.log2(len(words))

    # Generic character-class entropy
    charset = 0
    if any(c.islower() for c in passphrase):
        charset += 26
    if any(c.isupper() for c in passphrase):
        charset += 26
    if any(c.isdigit() for c in passphrase):
        charset += 10
    if any(not c.isalnum() for c in passphrase):
        charset += 32  # rough symbol estimate
    if charset == 0:
        return 0.0
    return len(passphrase) * math.log2(charset)
