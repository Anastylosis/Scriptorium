"""Where the work comes from, and what finishing a piece of it means.

Everything from `Worker.process_scene` down needs a path on disk, a list of
languages, and somewhere to say "this one is handled". Only those three
things are specific to Stash, so they live behind a library object and the
transcription side never learns which one it is talking to.

A library answers:

    bootstrap()        connect, create what is missing, fail loudly if it can't
    poll()             the scenes waiting, in the order to do them
    to_local(scene)    the path this container can open
    targets_for(scene) the languages that scene is asking for
    parent_of(scene)   the directory it lives in, as the library names it
    finish(scene, r)   record the outcome so it is not offered again
    flush(pending)     whatever has to happen once per batch of directories

`StashLibrary` is the original behaviour, lifted out of the worker unchanged.
`FolderLibrary`, in `folder.py`, watches a mount with no Stash at all.
"""

import logging
from pathlib import Path

from . import tags
from .paths import PathMapper
from .stash import Client

log = logging.getLogger(__name__)


def open_library(cfg, client=None, store=None):
    """The library this configuration asks for.

    WATCH_DIRS is the switch: STASH_URL has always had a default, so an
    unset one cannot mean "no Stash". Setting both is refused in config.
    """
    if cfg.watch.dirs:
        from .folder import FolderLibrary
        return FolderLibrary(cfg)
    return StashLibrary(cfg, client=client)


class StashLibrary:
    """Scenes carrying a `subs:<lang>` tag, in a Stash library."""

    def __init__(self, cfg, client=None):
        self.cfg = cfg
        self.client = client or Client(cfg.stash.url, cfg.stash.api_key)
        self.mapper = PathMapper(cfg.stash.path_from, cfg.stash.path_to)
        self.discover, self._discover_note = tags.discovery_enabled(cfg.tags)
        self.plan = tags.Plan()
        self.done_id = self.failed_id = ""

    def label(self):
        return f"Stash at {self.cfg.stash.url}"

    def waiting_hint(self):
        return None            # the page has wording of its own for Stash

    def requested(self):
        return self.plan.names()

    # -- setup ------------------------------------------------------------

    def bootstrap(self):
        self.client.probe_captions()
        self.done_id, self.failed_id = tags.bootstrap(self.client, self.cfg.tags)
        if self._discover_note:
            log.info("%s", self._discover_note)
        self.refresh_plan()

    def refresh_plan(self):
        if self.discover:
            self.plan = tags.discover(self.client, self.cfg.tags,
                                      self.done_id, self.failed_id,
                                      previous=self.plan)
        else:
            self.plan = tags.fixed(self.client, self.cfg.tags,
                                   self.done_id, self.failed_id)
        return self.plan

    # -- the queue --------------------------------------------------------

    def poll(self):
        # Re-read the tag set each poll so a language tag created since
        # startup is honoured without a restart.
        plan = self.refresh_plan()
        return self.client.find_tagged_scenes(plan.ids) if plan.ids else []

    def to_local(self, scene):
        return self.mapper.to_local(scene["files"][0]["path"])

    def targets_for(self, scene):
        """The languages this scene is asking for, by tag id.

        Matched on id against the plan used for the query, not by re-parsing
        names, so a tag created mid-scene cannot be mistaken for one we acted on.

        Deduplicated: `subs:en` and `subs:eng` are different tags naming the
        same language, and a scene carrying both should be transcribed once.
        """
        wanted = []
        for t in scene["tags"]:
            req = self.plan.requests.get(t["id"])
            if req is not None and req.lang not in wanted:
                wanted.append(req.lang)
        return wanted

    @staticmethod
    def parent_of(scene):
        """The directory Stash keeps the scene's file in.

        Stash's own path, not the mapped local one — this is what Stash is
        told to go and look at.
        """
        files = scene.get("files") or []
        return str(Path(files[0]["path"]).parent) if files else ""

    # -- finishing --------------------------------------------------------

    def finish(self, scene, result):
        """Replace the request tags with done/failed.

        The scene's tags are re-read first: sceneUpdate replaces the whole
        list, and the poll-time snapshot can be an hour old on a long scene,
        so writing it back would clobber anything added meanwhile.
        """
        handled = {t["id"] for t in scene["tags"] if t["id"] in self.plan.requests}
        try:
            current = self.client.scene_tags(scene["id"]) or scene["tags"]
        except Exception:
            current = scene["tags"]
        keep = [t["id"] for t in current if t["id"] not in handled]
        keep.append(self.done_id if result.ok else self.failed_id)
        self.client.set_scene_tags(scene["id"], sorted(set(keep)))

    def flush(self, pending):
        """Ask Stash to rescan the directories that gained a caption.

        One job for the batch rather than one per scene: the old shape had
        Stash rescanning a directory once per file written into it, which on a
        long queue keeps a scan running continuously against the same database
        the worker is still swapping tags in.
        """
        if not pending:
            return
        paths = sorted(pending)
        pending.clear()
        try:
            self.client.metadata_scan(paths)
        except Exception as e:
            log.error("  could not ask Stash to rescan %d path(s): %s",
                      len(paths), e)
