"""What happened to each language a scene asked for.

The distinction that matters is between "there was nothing to do" and "this
cannot be done". Both used to end with the scene tagged subs:done, so a
request the worker could never honour — a target needing an LLM with no
OLLAMA_URL set, say — looked identical to a finished one and the user's tag
was gone with only a log line to show for it.
"""

from typing import NamedTuple

WRITTEN = "written"          # a file was produced
SKIPPED = "skipped"          # already covered; nothing needed doing
NO_SPEECH = "no-speech"      # nothing to transcribe
DRY_RUN = "dry-run"          # would have been written
UNSUPPORTED = "unsupported"  # cannot be produced as configured
ERROR = "error"              # tried and failed

# Anything here means the request was not honoured and will not be on a
# retry without the user changing something, so the scene is a failure.
FAILURES = frozenset({UNSUPPORTED, ERROR})


class Target(NamedTuple):
    lang: str
    action: str
    detail: str = ""
    # A caption file Stash does not already list was written to disk. Not
    # tied to `action`: a target can fail at translation and still have left
    # a salvaged source-language transcript that needs attaching.
    new_caption: bool = False


class Scene(NamedTuple):
    targets: tuple = ()
    fatal: str = ""      # set when the scene failed before any target ran

    @property
    def ok(self) -> bool:
        if self.fatal:
            return False
        return not any(t.action in FAILURES for t in self.targets)

    @property
    def needs_scan(self) -> bool:
        # Keyed on a file having landed, not on the target having succeeded.
        # A failed translation still leaves a transcript, and skipping the
        # scan would leave it on disk where Stash never sees it.
        return any(t.new_caption for t in self.targets)

    def summary(self) -> str:
        if self.fatal:
            return self.fatal
        if not self.targets:
            return "nothing requested"
        return ", ".join(
            f"{t.lang}: {t.action}" + (f" ({t.detail})" if t.detail else "")
            for t in self.targets)


def failed(reason: str) -> Scene:
    return Scene(fatal=reason)
