"""Folder mode: a watched mount, marker files, and the ledger.

The library is exercised through its real seams — a real tree on disk and a
real ledger file — because every hazard here is a filesystem one.
"""

import json
import os
import time

import pytest

from scriptorium import config, outcomes, status
from scriptorium.folder import (
    FolderLibrary,
    Ledger,
    ignored_stash_settings,
    read_markers,
    sibling_captions,
)
from scriptorium.library import StashLibrary, open_library
from scriptorium.worker import Worker


def cfg_for(root, **env):
    return config.from_env({"WATCH_DIRS": str(root), "WATCH_MIN_AGE": "0",
                            "STATE_DIR": str(root / ".state"), **env})


def library(root, **env):
    lib = FolderLibrary(cfg_for(root, **env))
    lib.bootstrap()
    return lib


def video(path, text="video"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def targets(lib):
    return {os.path.basename(s["id"]): s["targets"] for s in lib.poll()}


# -- choosing the library -------------------------------------------------

def test_watch_dirs_selects_folder_mode(tmp_path):
    assert isinstance(open_library(cfg_for(tmp_path)), FolderLibrary)
    assert isinstance(open_library(config.from_env({}), client=object()),
                      StashLibrary)


def test_stash_url_and_watch_dirs_together_are_refused(tmp_path):
    with pytest.raises(config.ConfigError) as e:
        config.from_env({"WATCH_DIRS": str(tmp_path), "STASH_URL": "http://s"})
    assert "one library" in str(e.value)


def test_a_watched_directory_that_is_not_there_fails_at_startup(tmp_path):
    lib = FolderLibrary(cfg_for(tmp_path / "nope"))
    with pytest.raises(RuntimeError) as e:
        lib.bootstrap()
    assert "Check the mount" in str(e.value)


# -- marker files ---------------------------------------------------------

def test_markers_name_directory_and_per_file_requests():
    here, per_file, bad = read_markers(
        ["subs.en", "subs.es", "otra.subs.auto", "otra.mkv.subs.fr", "clip.mkv"])
    assert here == ["en", "es"]
    assert per_file == {"otra": ["auto"], "otra.mkv": ["fr"]}
    assert bad == []


def test_a_marker_stash_could_never_attach_is_rejected():
    here, _, bad = read_markers(["subs.pt-BR", "subs.xx", "subs.eng"])
    assert here == ["en"], "three-letter codes normalise, like the tags do"
    assert sorted(bad) == ["subs.pt-BR", "subs.xx"]


def test_no_marker_anywhere_means_the_spoken_language(tmp_path):
    video(tmp_path / "clip.mkv")
    assert targets(library(tmp_path)) == {"clip.mkv": ["auto"]}


def test_watch_langs_sets_the_default(tmp_path):
    video(tmp_path / "clip.mkv")
    assert targets(library(tmp_path, WATCH_LANGS="en,es")) == {"clip.mkv": ["en", "es"]}


def test_a_directory_marker_covers_everything_under_it(tmp_path):
    video(tmp_path / "clip.mkv")
    video(tmp_path / "deep" / "other.mp4")
    (tmp_path / "subs.en").write_text("")
    assert targets(library(tmp_path)) == {"clip.mkv": ["en"], "other.mp4": ["en"]}


def test_the_nearest_marker_wins(tmp_path):
    # Replacing rather than adding: a marker further down is how you say
    # "not that, this" about one shelf of the library.
    video(tmp_path / "clip.mkv")
    (tmp_path / "subs.en").write_text("")
    video(tmp_path / "spanish" / "pelicula.mkv")
    (tmp_path / "spanish" / "subs.es").write_text("")
    assert targets(library(tmp_path)) == {"clip.mkv": ["en"], "pelicula.mkv": ["es"]}


def test_a_per_file_marker_overrides_its_directory(tmp_path):
    video(tmp_path / "one.mkv")
    video(tmp_path / "two.mkv")
    (tmp_path / "subs.en").write_text("")
    (tmp_path / "two.subs.auto").write_text("")
    assert targets(library(tmp_path)) == {"one.mkv": ["en"], "two.mkv": ["auto"]}


def test_a_per_file_marker_may_carry_the_video_extension(tmp_path):
    video(tmp_path / "two.mkv")
    (tmp_path / "two.mkv.subs.fr").write_text("")
    assert targets(library(tmp_path)) == {"two.mkv": ["fr"]}


def test_only_video_files_and_visible_directories_are_offered(tmp_path):
    video(tmp_path / "clip.mkv")
    video(tmp_path / "notes.txt")
    video(tmp_path / ".trash" / "deleted.mkv")
    assert list(targets(library(tmp_path))) == ["clip.mkv"]


def test_a_file_still_being_copied_in_is_left_alone(tmp_path):
    video(tmp_path / "clip.mkv")
    lib = library(tmp_path, WATCH_MIN_AGE="600")
    assert lib.poll() == []


# -- what is already on disk ----------------------------------------------

def test_sibling_subtitles_are_reported_the_way_stash_reports_captions(tmp_path):
    path = video(tmp_path / "clip.mkv")
    (tmp_path / "clip.en.srt").write_text("")
    (tmp_path / "clip.eng.vtt").write_text("")
    (tmp_path / "clip.srt").write_text("")          # no language: not a caption
    (tmp_path / "clip.pt-BR.srt").write_text("")    # Stash could not attach it
    assert sibling_captions(path) == [
        {"language_code": "en", "caption_type": "srt"},
        {"language_code": "eng", "caption_type": "vtt"},
    ]


def test_a_video_whose_name_holds_glob_characters_is_matched_literally(tmp_path):
    path = video(tmp_path / "clip [2024].mkv")
    (tmp_path / "clip [2024].en.srt").write_text("")
    assert sibling_captions(path) == [{"language_code": "en", "caption_type": "srt"}]


# -- the ledger -----------------------------------------------------------

def stat_of(path):
    st = path.stat()
    return st.st_size, st.st_mtime


def test_a_recorded_file_is_not_offered_again(tmp_path):
    path = video(tmp_path / "clip.mkv")
    out = tmp_path / "clip.en.srt"
    out.write_text("1\n")
    lib = library(tmp_path, WATCH_LANGS="en")
    size, mtime = stat_of(path)
    lib.ledger.record(path, size, mtime, ["en"], True, [out])
    assert lib.poll() == []


def test_a_changed_video_comes_back(tmp_path):
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path, WATCH_LANGS="en")
    size, mtime = stat_of(path)
    lib.ledger.record(path, size, mtime, ["en"], True, [])
    path.write_text("video, re-encoded")
    assert len(lib.poll()) == 1


def test_a_new_marker_reopens_a_finished_file(tmp_path):
    # The request grew. Nothing about the video changed, but nobody has
    # produced the language that was just asked for.
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path, WATCH_LANGS="en")
    size, mtime = stat_of(path)
    lib.ledger.record(path, size, mtime, ["en"], True, [])
    assert lib.poll() == []
    (tmp_path / "subs.es").write_text("")
    assert targets(lib) == {"clip.mkv": ["es"]}


def test_deleting_a_subtitle_asks_for_it_again(tmp_path):
    path = video(tmp_path / "clip.mkv")
    out = tmp_path / "clip.en.srt"
    out.write_text("1\n")
    lib = library(tmp_path, WATCH_LANGS="en")
    size, mtime = stat_of(path)
    lib.ledger.record(path, size, mtime, ["en"], True, [out])
    out.unlink()
    assert len(lib.poll()) == 1


def test_a_failure_is_remembered_the_way_subs_failed_is(tmp_path):
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path, WATCH_LANGS="en")
    size, mtime = stat_of(path)
    lib.ledger.record(path, size, mtime, ["en"], False, [])
    assert lib.poll() == []

    retry = library(tmp_path, WATCH_LANGS="en", WATCH_RETRY_FAILED="1")
    assert len(retry.poll()) == 1


def test_the_ledger_survives_a_restart(tmp_path):
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path, WATCH_LANGS="en")
    size, mtime = stat_of(path)
    lib.ledger.record(path, size, mtime, ["en"], True, [])
    assert library(tmp_path, WATCH_LANGS="en").poll() == []


def test_an_unreadable_ledger_is_not_a_reason_to_refuse_to_start(tmp_path):
    state = tmp_path / ".state"
    state.mkdir()
    (state / "ledger.json").write_text("{not json")
    video(tmp_path / "clip.mkv")
    assert len(library(tmp_path).poll()) == 1


def test_finishing_records_what_was_written(tmp_path):
    path = video(tmp_path / "clip.mkv")
    (tmp_path / "clip.en.srt").write_text("1\n")
    lib = library(tmp_path, WATCH_LANGS="en")
    scene = lib.poll()[0]
    lib.finish(scene, outcomes.Scene(
        targets=(outcomes.Target("en", outcomes.WRITTEN, "3 cues"),),
        wrote=(tmp_path / "clip.en.srt",)))

    entry = json.loads((tmp_path / ".state" / "ledger.json").read_text())[str(path)]
    assert entry["result"] == "ok"
    assert entry["requested"] == ["en"]
    assert entry["wrote"] == [str(tmp_path / "clip.en.srt")]
    assert lib.poll() == []


def test_the_stat_recorded_is_the_one_the_job_was_queued_with(tmp_path):
    # A file that changed while it was being transcribed has not been done:
    # recording the new mtime would bury the new content forever.
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path, WATCH_LANGS="en")
    scene = lib.poll()[0]
    time.sleep(0.01)
    path.write_text("video, longer now")
    lib.finish(scene, outcomes.Scene(
        targets=(outcomes.Target("en", outcomes.WRITTEN),)))
    assert len(lib.poll()) == 1


# -- through the worker ---------------------------------------------------

def test_the_queue_loop_runs_with_no_stash_at_all(tmp_path, monkeypatch):
    video(tmp_path / "clip.mkv")
    cfg = cfg_for(tmp_path, RUN_ONCE="1")
    store = status.Store()
    w = Worker(cfg, store, library=FolderLibrary(cfg))
    w.library.bootstrap()
    seen = []

    def fake(scene):
        seen.append(scene["id"])
        return outcomes.Scene(targets=(outcomes.Target("es", outcomes.WRITTEN),))

    monkeypatch.setattr(w, "process_scene", fake)
    assert w.run() == 0
    assert seen == [str(tmp_path / "clip.mkv")]
    # And the scene is not offered a second time.
    assert w.library.poll() == []


def test_the_page_says_how_work_arrives_here(tmp_path):
    lib = library(tmp_path)
    assert "subs.en" in lib.waiting_hint()
    assert str(tmp_path) in lib.label()


def test_the_state_directory_is_made_at_startup(tmp_path):
    library(tmp_path)
    assert (tmp_path / ".state").is_dir()


def test_a_state_directory_that_cannot_exist_fails_loudly(tmp_path):
    # Discovered at startup rather than after the first transcription, when
    # the alternative is listening to the whole library again every poll.
    (tmp_path / "blocked").write_text("not a directory")
    lib = FolderLibrary(config.from_env({
        "WATCH_DIRS": str(tmp_path), "WATCH_MIN_AGE": "0",
        "STATE_DIR": str(tmp_path / "blocked" / "state")}))
    with pytest.raises(RuntimeError) as e:
        lib.bootstrap()
    assert "STATE_DIR" in str(e.value)


def test_the_ledger_is_never_written_into_the_media_folder(tmp_path):
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path)
    lib.ledger.record(path, 1, 1, ["auto"], True, [])
    assert sorted(p.name for p in tmp_path.iterdir()) == [".state", "clip.mkv"]


def test_ledger_entries_are_json_a_person_can_read(tmp_path):
    lib = library(tmp_path)
    lib.ledger.record(tmp_path / "clip.mkv", 10, 20, ["auto"], True, [])
    text = (tmp_path / ".state" / "ledger.json").read_text()
    assert "\n" in text and json.loads(text)


def test_an_empty_ledger_offers_everything(tmp_path):
    video(tmp_path / "a.mkv")
    video(tmp_path / "b.mp4")
    assert len(library(tmp_path).poll()) == 2


def test_the_ledger_is_keyed_per_file_not_per_directory(tmp_path):
    a = video(tmp_path / "a.mkv")
    video(tmp_path / "b.mkv")
    lib = library(tmp_path)
    lib.ledger.record(a, *stat_of(a), ["auto"], True, [])
    assert [os.path.basename(s["id"]) for s in lib.poll()] == ["b.mkv"]


def test_ledger_handles_a_missing_entry_gracefully(tmp_path):
    ledger = Ledger(tmp_path / "ledger.json").load()
    assert ledger.handled(tmp_path / "x.mkv", 1, 1, ["en"]) is False


# -- keeping the ledger honest --------------------------------------------

def test_a_deleted_video_is_forgotten(tmp_path):
    # Otherwise the ledger is a list of everything that ever passed through,
    # growing for the life of the install.
    gone = video(tmp_path / "gone.mkv")
    video(tmp_path / "stays.mkv")
    lib = library(tmp_path)
    for p in (gone, tmp_path / "stays.mkv"):
        lib.ledger.record(p, *stat_of(p), ["auto"], True, [])
    gone.unlink()
    lib.poll()
    assert list(lib.ledger.entries) == [str(tmp_path / "stays.mkv")]


def test_a_mount_that_dropped_out_is_not_forgotten(tmp_path):
    # An unmounted directory reads as an empty one. Pruning on that would
    # re-transcribe the whole library when the mount came back.
    path = video(tmp_path / "clip.mkv")
    lib = library(tmp_path)
    lib.ledger.record(path, *stat_of(path), ["auto"], True, [])
    path.unlink()                       # the whole root now looks empty
    lib.poll()
    assert list(lib.ledger.entries) == [str(path)]


def test_a_file_too_young_to_start_still_counts_as_present(tmp_path):
    path = video(tmp_path / "clip.mkv")
    video(tmp_path / "other.mkv")
    lib = library(tmp_path, WATCH_MIN_AGE="600")
    lib.ledger.record(path, *stat_of(path), ["auto"], True, [])
    lib.poll()
    assert list(lib.ledger.entries) == [str(path)]


def test_an_entry_outside_the_watched_roots_is_left_alone(tmp_path):
    # Narrowing WATCH_DIRS is not a statement about what is outside it.
    video(tmp_path / "media" / "clip.mkv")
    elsewhere = str(tmp_path / "other" / "old.mkv")
    lib = library(tmp_path / "media")
    lib.ledger.entries[elsewhere] = {"size": 1, "mtime": 1, "requested": [],
                                     "result": "ok", "wrote": []}
    lib.poll()
    assert elsewhere in lib.ledger.entries


def test_nested_roots_offer_a_video_once(tmp_path):
    video(tmp_path / "films" / "clip.mkv")
    cfg = config.from_env({
        "WATCH_DIRS": f"{tmp_path},{tmp_path / 'films'}",
        "WATCH_MIN_AGE": "0", "STATE_DIR": str(tmp_path / ".state")})
    lib = FolderLibrary(cfg)
    lib.bootstrap()
    assert [s["id"] for s in lib.poll()] == [str(tmp_path / "films" / "clip.mkv")]


def test_the_salvaged_transcript_is_recorded_too(tmp_path):
    # It is written under a language nobody asked for, by a target that then
    # failed, so reading the target list would miss it and deleting the file
    # would not bring the video back.
    path = video(tmp_path / "clip.mkv")
    salvage = tmp_path / "clip.ja.srt"
    salvage.write_text("1\n")
    lib = library(tmp_path, WATCH_LANGS="en")
    scene = lib.poll()[0]
    lib.finish(scene, outcomes.Scene(
        targets=(outcomes.Target("en", outcomes.ERROR, "translation failed",
                                 new_caption=True),),
        wrote=(salvage,)))
    entry = lib.ledger.entries[str(path)]
    assert entry["result"] == "failed"
    assert entry["wrote"] == [str(salvage)]


# -- settings that cannot mean anything here ------------------------------

def test_stash_only_settings_are_named_not_silently_dropped(tmp_path):
    cfg = cfg_for(tmp_path, PATH_FROM="/data", PATH_TO="/mnt",
                  STASH_API_KEY="secret", REQUEST_TAGS="subs:en")
    assert ignored_stash_settings(cfg) == ["STASH_API_KEY", "PATH_TO",
                                           "REQUEST_TAGS"]


def test_an_ordinary_folder_config_has_nothing_to_warn_about(tmp_path):
    assert ignored_stash_settings(cfg_for(tmp_path)) == []
