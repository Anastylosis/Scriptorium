"""What Stash already has, and what it still needs to be told about.

Stash lists a caption per (language, extension) on the scene. Two things
follow from that:

Work already done can be skipped — including under a different spelling of
the same language, since a hand-placed `foo.eng.srt` makes `foo.en.srt`
pointless.

A rescan is only needed for a genuinely new pair. Overwriting a caption Stash
already knows about is served straight from disk, so triggering a scan for it
just makes Stash walk a directory for nothing.

A registered caption is not proof the file is still there, so anything that
decides to skip must confirm the file exists.
"""

from . import langs, subtitles

# Stash files a caption with no language suffix under this marker.
UNKNOWN = "00"


def _entries(scene):
    return scene.get("captions") or []


def registered(scene, lang, ext="srt") -> bool:
    """Stash already lists a caption for this language and extension."""
    for cap in _entries(scene):
        if (cap.get("caption_type") or "").lower() != ext:
            continue
        code = cap.get("language_code")
        if code == UNKNOWN or not code:
            continue
        if langs.equivalent(code, lang):
            return True
    return False


def existing_file(video, scene, lang, ext="srt"):
    """The path of a caption Stash lists for this language that is really on
    disk, or None. Used to avoid writing `foo.en.srt` beside an equivalent
    `foo.eng.srt` that already covers the scene."""
    for cap in _entries(scene):
        if (cap.get("caption_type") or "").lower() != ext:
            continue
        code = cap.get("language_code")
        if code == UNKNOWN or not code:
            continue
        if not langs.equivalent(code, lang):
            continue
        path = subtitles.dest_for(video, code, ext)
        if path.exists():
            return path
    return None
