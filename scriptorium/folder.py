"""Watching a mount, with no Stash anywhere.

The request that a tag carries in a Stash library is carried here by an empty
file named `subs.<lang>`:

    /media/spanish/subs.en        everything under /media/spanish wants English
    /media/spanish/subs.es        ...and Spanish; several may sit together
    /media/spanish/otra.subs.auto just this video, source language only

The nearest directory holding any marker wins, so a marker deeper in the tree
replaces the one above it rather than adding to it — the same way you would
expect a directory-level setting to behave. A video with its own marker
ignores the directory entirely. With no marker anywhere, `WATCH_LANGS`
decides, and its default is `auto`: transcribe whatever is spoken.

What a tag also gave us was a record of the work: `subs:done` is why a scene
is not offered twice. Disk alone cannot replace it. An `auto` request has no
destination filename until the detector has run, so every poll would reload
the model and listen to every file again, and a file with no speech in it
would be retried forever. Hence the ledger, which is that record and nothing
more — deleting it costs a re-detection, never a subtitle.
"""

import glob
import json
import logging
import os
import time
from pathlib import Path

from . import langs, subtitles
from .tags import AUTO

log = logging.getLogger(__name__)

MARKER = "subs."
# Written into STATE_DIR, never beside the media: it is our bookkeeping, and
# a media folder should hold what a player can use.
LEDGER_NAME = "ledger.json"


def _lang(text):
    """The language a marker names, or None."""
    text = text.strip().lower()
    if text == AUTO:
        return AUTO
    return langs.normalize(text)


def read_markers(names):
    """(directory langs, {key: langs}, unreadable marker names).

    A per-file marker is keyed on both spellings a user might reach for —
    `otra.subs.en` and `otra.mkv.subs.en` — so neither is a silent no-op.
    """
    here, per_file, bad = [], {}, []
    for name in sorted(names):
        if name.startswith(MARKER):
            lang = _lang(name[len(MARKER):])
            if lang is None:
                bad.append(name)
            elif lang not in here:
                here.append(lang)
            continue
        key, sep, suffix = name.rpartition("." + MARKER)
        if not sep:
            continue
        lang = _lang(suffix)
        if lang is None:
            bad.append(name)
            continue
        langs_for = per_file.setdefault(key, [])
        if lang not in langs_for:
            langs_for.append(lang)
    return here, per_file, bad


def ignored_stash_settings(cfg):
    """Stash-only settings that are set but can mean nothing here.

    Compared against the defaults rather than read from the environment,
    because config has already been through `from_env` by the time anyone
    asks. Someone moving a working compose file across is the case this is
    for: PATH_FROM silently doing nothing looks exactly like PATH_FROM
    working, right up until the paths are wrong.
    """
    from .config import StashCfg, TagsCfg
    stash, tags = StashCfg(), TagsCfg()
    checks = [
        ("STASH_API_KEY", bool(cfg.stash.api_key)),
        ("PATH_FROM", cfg.stash.path_from != stash.path_from),
        ("PATH_TO", cfg.stash.path_to != stash.path_to),
        ("REQUEST_TAGS", cfg.tags.request_explicit),
        ("TAG_DISCOVERY", cfg.tags.discover != tags.discover),
        ("CREATE_TAGS", cfg.tags.create != tags.create),
        ("IGNORE_TAGS", bool(cfg.tags.ignore)),
        ("DONE_TAG", cfg.tags.done != tags.done),
        ("FAILED_TAG", cfg.tags.failed != tags.failed),
    ]
    return [name for name, set_ in checks if set_]


class Ledger:
    """What has already been done, keyed on the file as it was when we did it.

    An entry is only trusted while the video is unchanged, the request has not
    grown, and the files it claims to have written are still there. A
    re-encode, a new `subs.<lang>` marker, or a deleted subtitle each put the
    video back in the queue on the next poll.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.entries = {}

    def load(self):
        try:
            self.entries = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self.entries = {}
        except (OSError, ValueError) as e:
            # Losing the ledger costs re-detection, not subtitles; refusing to
            # start over a corrupt bookkeeping file would cost more.
            log.warning("ignoring unreadable ledger %s: %s", self.path, e)
            self.entries = {}
        return self

    def save(self):
        subtitles.write_text(
            json.dumps(self.entries, ensure_ascii=False, sort_keys=True,
                       indent=1),
            self.path)

    def handled(self, path, size, mtime, requested, retry_failed=False):
        e = self.entries.get(str(path))
        if not e:
            return False
        if e.get("size") != size or int(e.get("mtime", -1)) != int(mtime):
            return False
        if not set(requested) <= set(e.get("requested") or []):
            return False
        if retry_failed and e.get("result") != "ok":
            return False
        return all(Path(p).exists() for p in e.get("wrote") or [])

    def record(self, path, size, mtime, requested, ok, wrote):
        self.entries[str(path)] = {
            "size": size,
            "mtime": int(mtime),
            "requested": list(requested),
            "result": "ok" if ok else "failed",
            "wrote": [str(p) for p in wrote],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self.save()


class FolderLibrary:
    """Videos under a watched mount, with `subs.<lang>` files for requests."""

    def __init__(self, cfg, ledger=None):
        self.cfg = cfg
        self.roots = [Path(d) for d in cfg.watch.dirs]
        self.extensions = {"." + e.lstrip(".").lower()
                           for e in cfg.watch.extensions}
        self.ledger = ledger or Ledger(Path(cfg.watch.state_dir) / LEDGER_NAME)
        self._warned = set()
        self._seen_langs = set()

    def label(self):
        return "folder mode, watching " + ", ".join(str(r) for r in self.roots)

    def waiting_hint(self):
        where = str(self.roots[0]) if self.roots else "the watched folder"
        return (f"Put a video under {where} and it will be picked up. "
                f"An empty file named subs.en beside it asks for English; "
                f"with no marker the spoken language is transcribed.")

    def requested(self):
        return sorted(self._seen_langs)

    # -- setup ------------------------------------------------------------

    def bootstrap(self):
        missing = [r for r in self.roots if not r.is_dir()]
        if len(missing) == len(self.roots):
            raise RuntimeError(
                f"none of the watched directories exist: "
                f"{', '.join(str(m) for m in missing)}. Check the mount.")
        for m in missing:
            log.warning("watched directory %s does not exist, skipping", m)
        state = Path(self.cfg.watch.state_dir)
        try:
            state.mkdir(parents=True, exist_ok=True)
            probe = state / ".writable"
            probe.write_text("")
            probe.unlink()
        except OSError as e:
            raise RuntimeError(
                f"STATE_DIR {state} is not writable ({e}). Without it every "
                f"poll would listen to every file again.") from None
        self.ledger.load()
        # The label is logged at startup; say only what it leaves out.
        log.info("default request: %s (for anything with no subs.<lang> "
                 "marker near it)", ", ".join(self.cfg.watch.langs))
        # A ledger on the container filesystem works perfectly until the
        # container is recreated, and then the whole library is listened to
        # again. That is a mount people forget, so name the file.
        log.info("ledger: %s (%d file(s) already done) — keep it on a mount "
                 "or the library is re-examined after every rebuild",
                 self.ledger.path, len(self.ledger.entries))
        ignored = ignored_stash_settings(self.cfg)
        if ignored:
            log.warning("ignoring %s: folder mode never talks to Stash",
                        ", ".join(ignored))

    # -- the queue --------------------------------------------------------

    def poll(self):
        scenes, seen, alive = [], set(), []
        for root in self.roots:
            if not root.is_dir():
                continue
            found, here = self._walk(root)
            # Nested roots — /media and /media/films — offer the same video
            # twice, and the ledger only learns about it once the first copy
            # has been transcribed.
            for scene in found:
                if scene["id"] not in seen:
                    scenes.append(scene)
            seen |= here
            if here:
                alive.append(root)
        self._forget_missing(seen, alive)
        self._seen_langs = {lang for s in scenes for lang in s["targets"]}
        return scenes

    def _forget_missing(self, seen, alive):
        """Drop entries for videos that are no longer there.

        Only under a root that produced at least one file this poll. A mount
        that has dropped out reads as an empty directory, not an error, and
        forgetting a library because its NFS server blinked would re-transcribe
        every file on it when the mount came back.
        """
        gone = [p for p in self.ledger.entries
                if p not in seen
                and any(Path(p).is_relative_to(r) for r in alive)]
        if not gone:
            return
        for p in gone:
            del self.ledger.entries[p]
        self.ledger.save()
        log.info("forgot %d ledger entr%s for files no longer on disk",
                 len(gone), "y" if len(gone) == 1 else "ies")

    def _walk(self, root):
        """(scenes to do, every video path seen under this root)."""
        cfg = self.cfg.watch
        inherited = {str(root.parent): list(cfg.langs)}
        found, seen = [], set()
        for dirpath, dirnames, filenames in os.walk(root):
            # A dotted directory is somebody's metadata, not a library.
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            here, per_file, bad = read_markers(filenames)
            for name in bad:
                self._warn(os.path.join(dirpath, name))
            wanted = here or inherited.get(os.path.dirname(dirpath),
                                           list(cfg.langs))
            inherited[dirpath] = wanted

            for name in sorted(filenames):
                path = Path(dirpath) / name
                if path.suffix.lower() not in self.extensions:
                    continue
                # Counted before anything filters it: a file the ledger has
                # already done is still a file that is there, and that is
                # what stops it being forgotten.
                seen.add(str(path))
                targets = (per_file.get(name)
                           or per_file.get(path.with_suffix("").name)
                           or wanted)
                scene = self._scene(path, targets)
                if scene is not None:
                    found.append(scene)
        return found, seen

    def _scene(self, path, targets):
        """A scene-shaped job, or None when this file is not ours to do."""
        try:
            st = path.stat()
        except OSError:
            return None
        # A file still being copied in is not a file yet: half an mkv gives a
        # short transcript, and nothing would ever come back to finish it.
        age = time.time() - st.st_mtime
        if age < self.cfg.watch.min_age:
            log.debug("%s changed %ds ago, leaving it to settle", path, age)
            return None
        if self.ledger.handled(path, st.st_size, st.st_mtime, targets,
                               self.cfg.watch.retry_failed):
            return None
        return {
            "id": str(path),
            "title": path.name,
            "files": [{"path": str(path), "duration": None}],
            "tags": [],
            # Read off the disk, so the same "already covered by foo.eng.srt"
            # reasoning the Stash library gets from its caption list applies
            # here with nothing in the worker having to know the difference.
            "captions": sibling_captions(path),
            "targets": list(targets),
            "size": st.st_size,
            "mtime": st.st_mtime,
        }

    def to_local(self, scene):
        return Path(scene["files"][0]["path"])

    def targets_for(self, scene):
        return list(scene.get("targets") or [])

    @staticmethod
    def parent_of(scene):
        return str(Path(scene["files"][0]["path"]).parent)

    # -- finishing --------------------------------------------------------

    def finish(self, scene, result):
        path = Path(scene["files"][0]["path"])
        # The stat taken when the job was queued, not a fresh one: a file
        # that changed while we were transcribing it has not been done.
        self.ledger.record(path, scene["size"], scene["mtime"],
                           self.targets_for(scene), result.ok,
                           [p for p in result.wrote if Path(p).exists()])

    def flush(self, pending):
        # Nothing to tell: the player reads the folder we just wrote into.
        pending.clear()

    # -- noise control ----------------------------------------------------

    def _warn(self, path):
        if path in self._warned:
            return
        self._warned.add(path)
        name = os.path.basename(path)
        code = name.rpartition(MARKER)[2]
        log.warning("ignoring marker %s: %s", name, langs.reject_reason(code))


def sibling_captions(video):
    """Caption entries for the subtitle files already sitting beside a video.

    Shaped like Stash's `captions` list, because that is what reads it.
    """
    stem = video.with_suffix("").name
    out = []
    for ext in ("srt", "vtt"):
        pattern = f"{glob.escape(stem)}.*.{ext}"
        for path in sorted(video.parent.glob(pattern)):
            code = path.name[len(stem) + 1:-(len(ext) + 1)]
            if langs.is_caption_suffix(code):
                out.append({"language_code": code, "caption_type": ext})
    return out
