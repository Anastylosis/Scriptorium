"""The source-language transcript is written once, whoever asks for it first.

A scene tagged `subs:en` + `subs:ja` with Japanese audio has two routes to the
same `.ja.srt`: the salvage write that guards the LLM call, and `subs:ja` as a
target in its own right. Both used to run, so the second rewrote the first
one's bytes and the status page listed the file twice.
"""

from scriptorium import config, outcomes, status, tags
from scriptorium.worker import Worker

CUES = [(1.0, 3.0, "ネコ"), (4.0, 6.0, "イヌ")]
ENGLISH = [(1.0, 3.0, "cat"), (4.0, 6.0, "dog")]


class Models:
    """Whisper, minus Whisper. Counts what it was asked to do."""

    def __init__(self, src="ja"):
        self.src = src
        self.transcribes = 0

    def detect_language(self, path, duration):
        return self.src, 1.0

    def transcribe(self, path, language, task="transcribe", model=None,
                   on_progress=None):
        self.transcribes += 1
        return list(CUES), None


class Ollama:
    def __init__(self):
        self.calls = 0

    def translate(self, cues, src, dst, on_progress=None):
        self.calls += 1
        return list(ENGLISH)


def scene(video, tag_ids):
    return {"id": "1",
            "files": [{"path": str(video), "duration": 60.0}],
            "tags": [{"id": t} for t in tag_ids],
            "captions": []}


def run(tmp_path, tag_ids, env=None):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    w = Worker(config.from_env({"OLLAMA_URL": "http://ollama",
                                "MODEL": "large-v3-turbo",
                                "REGENERATE": "if-ours", **(env or {})}),
               status.Store(), client=object())
    w.models, w.ollama = Models(), Ollama()
    w.plan = tags.Plan(requests={
        "t-en": tags.RequestTag("t-en", "subs:en", "en"),
        "t-ja": tags.RequestTag("t-ja", "subs:ja", "ja"),
        "t-auto": tags.RequestTag("t-auto", "subs:auto", tags.AUTO),
    })
    result = w.process_scene(scene(video, tag_ids))
    rows = [c["what"] for c in w.store.snapshot()["completed"]]
    return w, result, rows


def test_the_salvage_and_the_source_target_write_one_file(tmp_path):
    # subs:en first: the salvage write lands the transcript before the LLM
    # call, and subs:ja then finds its own output already on disk.
    w, _, rows = run(tmp_path, ["t-en", "t-ja"])
    assert rows == ["clip.ja.srt — 2 cues", "clip.en.srt — 2 cues"]
    assert w.ollama.calls == 1


def test_the_order_of_the_tags_does_not_change_that(tmp_path):
    # subs:ja first: now the target writes and the salvage is the redundant
    # one. It still may not be skipped outright — it is what survives a crash
    # mid-translation when the transcript is not itself a target.
    _, _, rows = run(tmp_path, ["t-ja", "t-en"])
    assert rows == ["clip.ja.srt — 2 cues", "clip.en.srt — 2 cues"]


def test_both_targets_still_report_written(tmp_path):
    # The file was produced for this scene; which of the two calls put it
    # there is not something the scene outcome should care about, or a new
    # language would go unscanned and never attach.
    _, result, _ = run(tmp_path, ["t-en", "t-ja"])
    assert [(t.lang, t.action) for t in result.targets] == [
        ("en", outcomes.WRITTEN), ("ja", outcomes.WRITTEN)]
    assert result.needs_scan


def test_the_audio_is_only_transcribed_once(tmp_path):
    w, _, _ = run(tmp_path, ["t-en", "t-ja"])
    assert w.models.transcribes == 1


def test_the_next_scene_writes_its_own_copy(tmp_path):
    # The record is per scene, not per worker: two scenes in a directory
    # produce two files, and the second must not be swallowed.
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"")
    other = tmp_path / "other.mp4"
    other.write_bytes(b"")
    w, _, _ = run(tmp_path, ["t-ja"])
    w.process_scene(scene(other, ["t-ja"]))
    assert (tmp_path / "other.ja.srt").exists()
    rows = [c["what"] for c in w.store.snapshot()["completed"]]
    assert rows == ["clip.ja.srt — 2 cues", "other.ja.srt — 2 cues"]


def test_an_auto_target_is_named_by_the_language_it_resolved_to(tmp_path):
    # The status page was printing the literal string "auto" where a language
    # belongs, and could never mark the one in hand: `target` holds the
    # resolved code, so it matched nothing in the list beside it.
    w, _, _ = run(tmp_path, ["t-auto"])
    assert w.store.snapshot()["targets"] == ["ja"]


def test_a_language_asked_for_twice_is_only_named_once(tmp_path):
    # subs:ja and subs:auto on Japanese audio are the same request.
    w, _, _ = run(tmp_path, ["t-ja", "t-auto"])
    assert w.store.snapshot()["targets"] == ["ja"]
