"""Translating between the path Stash reports and the path we can open."""

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PathMapper:
    frm: str = "/data"
    to: str = "/data"

    def to_local(self, stash_path: str) -> Path:
        """Map a Stash-reported path onto this container's filesystem.

        Anchored at a path boundary: a plain string replace would rewrite
        `/mnt/data/x.mp4` when mapping `/data`, because `/data` occurs in
        the middle of it.
        """
        frm, to = self.frm.rstrip("/"), self.to.rstrip("/")
        if not frm or frm == to:
            return Path(stash_path)
        if stash_path == frm:
            return Path(to)
        if stash_path.startswith(frm + "/"):
            return Path(to + stash_path[len(frm):])
        log.debug("no path mapping applies to %s", stash_path)
        return Path(stash_path)

    def to_stash(self, local: Path | str) -> str:
        local = str(local)
        frm, to = self.frm.rstrip("/"), self.to.rstrip("/")
        if not to or frm == to:
            return local
        if local == to:
            return frm
        if local.startswith(to + "/"):
            return frm + local[len(to):]
        return local
