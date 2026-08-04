"""Language codes.

Two separate questions, deliberately not collapsed into one predicate:

`is_caption_suffix` — will Stash attach `<video>.<code>.srt` to the video?
Stash parses the suffix with x/text's ParseBase, which takes a bare two- or
three-letter subtag and nothing else. A regional subtag such as `pt-BR` does
not parse, so Stash treats it as part of the filename and the caption
silently never attaches to the scene.

`whisper_supports` — can Whisper transcribe it? Whisper knows 100 languages.
`fil` is a perfectly legal caption suffix Whisper has never heard of (it has
`tl`), so a tag can be valid and still unproducible.
"""

import re

from ._langtable import ALIAS, NAMES, WHISPER

_BARE = re.compile(r"^[a-z]{2,3}$")


def normalize(code):
    """Canonical lowercase code, or None if this is not a bare ISO 639 subtag."""
    if not code:
        return None
    code = code.strip().lower()
    if not _BARE.match(code):
        return None
    if code in NAMES:
        return code
    return ALIAS.get(code)


def is_caption_suffix(code) -> bool:
    return normalize(code) is not None


def whisper_supports(code) -> bool:
    c = normalize(code)
    return c is not None and c in WHISPER


def name_of(code) -> str:
    c = normalize(code)
    return NAMES.get(c, code) if c else code


def equivalent(a, b) -> bool:
    """True when two codes name the same language. `en` and `eng` do; Stash's
    `00` unknown-language marker never matches a real code."""
    na, nb = normalize(a), normalize(b)
    return na is not None and na == nb


def has_subtag(code) -> bool:
    return bool(code) and bool(re.search(r"[-_]", code.strip()))


def reject_reason(code) -> str:
    """Why this code cannot be used, phrased for the log."""
    if has_subtag(code):
        base = code.strip().lower().split("-")[0].split("_")[0]
        hint = f" Use subs:{base}." if is_caption_suffix(base) else ""
        return (f"Stash cannot attach captions with a regional subtag "
                f"('.{code}.srt' is parsed as part of the filename, so the "
                f"caption silently never attaches).{hint}")
    return f"{code!r} is not an ISO 639 language code."


def nearest_whisper(code):
    """A supported code for the same language, if one exists — `fil` -> `tl`."""
    c = normalize(code)
    if c is None or c in WHISPER:
        return c
    name = NAMES.get(c, "").lower()
    for other in WHISPER:
        if NAMES.get(other, "").lower() == name:
            return other
    # Tagalog and Filipino are the same language under two names.
    if c == "fil":
        return "tl"
    return None
