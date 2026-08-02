"""When the real queue loop asks Stash to rescan."""

from stash_subs import config, outcomes, status, tags
from stash_subs.worker import Worker


class Client:
    """Serves one scene once, then an empty queue."""

    def __init__(self, scene):
        self.scene = scene
        self.scans = []
        self.updates = []
        self.supports_captions = True
        self._served = False

    def find_tagged_scenes(self, ids):
        if self._served:
            return []
        self._served = True
        return [self.scene]

    def scene_tags(self, scene_id):
        return self.scene["tags"]

    def set_scene_tags(self, scene_id, ids):
        self.updates.append((scene_id, list(ids)))

    def metadata_scan(self, path):
        self.scans.append(path)


def scene_with(*caps):
    return {"id": "1",
            "files": [{"path": "/data/clip.mp4", "duration": 60.0}],
            "tags": [],
            "captions": [{"language_code": c, "caption_type": t} for c, t in caps]}


def run_once(monkeypatch, scene, result, env=None):
    env = {"RUN_ONCE": "1", **(env or {})}
    client = Client(scene)
    w = Worker(config.from_env(env), status.Store(), client=client)
    w.plan = tags.Plan(requests={}, done_id="d", failed_id="f")
    w.done_id, w.failed_id = "d", "f"
    monkeypatch.setattr(w, "refresh_plan", lambda: w.plan)
    monkeypatch.setattr(w, "process_scene", lambda s: result)
    w.plan = tags.Plan(requests={"t": tags.RequestTag("t", "subs:en", "en")},
                       done_id="d", failed_id="f")
    w.run()
    return client


def test_a_new_language_asks_for_a_rescan(monkeypatch):
    c = run_once(monkeypatch, scene_with(), outcomes.Scene(targets=(outcomes.Target('en', outcomes.WRITTEN, new_caption=True),)))
    assert c.scans == ["/data"]


def test_rewriting_a_known_caption_does_not_rescan(monkeypatch):
    # Stash serves an already-registered caption straight from disk, so a
    # scan would only make it walk the directory for nothing.
    c = run_once(monkeypatch, scene_with(("en", "srt")),
                 outcomes.Scene(targets=(outcomes.Target('en', outcomes.WRITTEN, new_caption=False),)))
    assert c.scans == []
    assert c.updates[0][1] == ["d"], "still tagged done"


def test_a_failed_scene_is_tagged_failed_and_not_scanned(monkeypatch):
    c = run_once(monkeypatch, scene_with(), outcomes.failed('boom'))
    assert c.scans == []
    assert c.updates[0][1] == ["f"]


def test_the_scan_uses_the_stash_side_path(monkeypatch):
    # The worker may see the file at a different path; Stash must be given
    # the path Stash itself reported.
    c = run_once(monkeypatch, scene_with(), outcomes.Scene(targets=(outcomes.Target('en', outcomes.WRITTEN, new_caption=True),)),
                 env={"PATH_FROM": "/data", "PATH_TO": "/mnt/media"})
    assert c.scans == ["/data"]


def test_dry_run_touches_nothing(monkeypatch):
    c = run_once(monkeypatch, scene_with(), outcomes.Scene(targets=(outcomes.Target('en', outcomes.WRITTEN, new_caption=True),)),
                 env={"DRY_RUN": "1"})
    assert c.scans == []
    assert c.updates == []
