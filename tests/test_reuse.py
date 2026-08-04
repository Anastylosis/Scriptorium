"""Parsing existing subtitles, and translating from them instead of the audio."""

from scriptorium import config, status, subtitles
from scriptorium.subtitles import (
    MARKER,
    OLD_MARKER,
    Provenance,
    load,
    parse,
    render_srt,
    render_vtt,
    with_annotation,
    without_marker,
)
from scriptorium.worker import Worker

CUES = [(1.0, 3.25, "first line"), (10.5, 12.0, "second line")]
PROV = Provenance(asr_model="tiny", src="en", src_name="English",
                  dst="en", dst_name="English", date="2026-08-02")


# -- parsing ---------------------------------------------------------------

def test_srt_round_trips():
    assert parse(render_srt(CUES)) == CUES


def test_vtt_round_trips():
    assert parse(render_vtt(CUES)) == CUES


def test_a_vtt_note_block_is_not_read_as_a_cue():
    got = parse(render_vtt(CUES, note=PROV.as_json(cues=2)))
    assert got == CUES


def test_multi_line_cue_text_is_kept():
    body = "1\n00:00:01,000 --> 00:00:02,000\nline one\nline two\n\n"
    assert parse(body) == [(1.0, 2.0, "line one\nline two")]


def test_cue_numbers_and_identifiers_are_ignored():
    body = ("intro\n00:00:01,000 --> 00:00:02,000\nhello\n\n"
            "42\n00:00:03,000 --> 00:00:04,000\nworld\n\n")
    assert [c[2] for c in parse(body)] == ["hello", "world"]


def test_crlf_input_parses():
    assert parse(render_srt(CUES).replace("\n", "\r\n")) == CUES


def test_centisecond_fractions_are_scaled():
    assert parse("1\n00:00:01,50 --> 00:00:02,5\nx\n\n") == [(1.5, 2.5, "x")]


def test_garbage_yields_no_cues():
    assert parse("this is not a subtitle file") == []
    assert parse("") == []


# -- our own marker --------------------------------------------------------

def test_the_marker_cue_is_stripped():
    annotated = with_annotation(CUES, PROV, media_duration=20.0)
    assert len(annotated) == 3
    assert without_marker(annotated) == CUES


def test_loading_our_own_output_gives_back_the_original_cues(tmp_path):
    # Feeding the marker to the translator would translate it, and the
    # result would then be annotated again on the way out.
    f = tmp_path / "clip.en.srt"
    f.write_text(render_srt(with_annotation(CUES, PROV, media_duration=20.0)),
                 encoding="utf-8")
    assert load(f) == CUES
    assert all(MARKER not in c[2] for c in load(f))


def test_load_returns_none_for_a_missing_file(tmp_path):
    assert load(tmp_path / "nope.srt") is None


def test_load_returns_none_for_an_unparseable_file(tmp_path):
    f = tmp_path / "clip.en.srt"
    f.write_text("nothing useful here", encoding="utf-8")
    assert load(f) is None


def test_a_file_holding_only_our_marker_is_not_a_usable_source(tmp_path):
    f = tmp_path / "clip.en.srt"
    f.write_text(render_srt([(1.0, 3.0, f"{MARKER} generated")]), encoding="utf-8")
    assert load(f) is None


def test_without_marker_also_strips_the_pre_rename_marker():
    # Old cues carrying the marker this project shipped as stash-subs must
    # be recognised too, or a stale one would be fed to the translator.
    old_cue = (1.0, 3.0, f"{OLD_MARKER} generated")
    assert without_marker(CUES + [old_cue]) == CUES


def test_loading_pre_rename_output_strips_its_marker_too(tmp_path):
    f = tmp_path / "clip.en.srt"
    cues = CUES + [(20.0, 22.0, f"{OLD_MARKER} machine-generated")]
    f.write_text(render_srt(cues), encoding="utf-8")
    assert load(f) == CUES


# -- the worker uses it ----------------------------------------------------

def make_worker(env=None):
    w = Worker(config.from_env(env or {}), status.Store(), client=object())
    w.models = None          # any transcription attempt will raise
    return w


def test_an_existing_transcript_is_used_instead_of_transcribing(tmp_path):
    video = tmp_path / "clip.mp4"
    subtitles.write_srt(CUES, tmp_path / "clip.en.srt")
    w = make_worker()
    # models is None, so this raises if it tries to run Whisper.
    assert w._source_cues(video, "en", {}) == CUES


def test_our_own_annotated_output_is_reusable(tmp_path):
    video = tmp_path / "clip.mp4"
    subtitles.write_srt(with_annotation(CUES, PROV, media_duration=20.0),
                        tmp_path / "clip.en.srt")
    assert make_worker()._source_cues(video, "en", {}) == CUES


def test_the_scene_cache_wins_over_the_file(tmp_path):
    video = tmp_path / "clip.mp4"
    subtitles.write_srt(CUES, tmp_path / "clip.en.srt")
    cached = [(0.0, 1.0, "from this run")]
    assert make_worker()._source_cues(video, "en", {"en": cached}) == cached


def test_reuse_can_be_turned_off(tmp_path):
    import pytest
    video = tmp_path / "clip.mp4"
    subtitles.write_srt(CUES, tmp_path / "clip.en.srt")
    w = make_worker({"REUSE_TRANSCRIPT": "0"})
    with pytest.raises(AttributeError):
        w._source_cues(video, "en", {})     # went to Whisper, models is None


def test_a_transcript_in_another_language_is_not_used(tmp_path):
    import pytest
    video = tmp_path / "clip.mp4"
    subtitles.write_srt(CUES, tmp_path / "clip.fr.srt")
    with pytest.raises(AttributeError):
        make_worker()._source_cues(video, "en", {})


def test_vtt_is_accepted_as_a_source(tmp_path):
    video = tmp_path / "clip.mp4"
    (tmp_path / "clip.en.vtt").write_text(render_vtt(CUES), encoding="utf-8")
    w = make_worker({"OUTPUT_FORMATS": "vtt"})
    assert w._source_cues(video, "en", {}) == CUES


def test_regenerate_always_forces_a_fresh_transcript(tmp_path):
    import pytest
    video = tmp_path / "clip.mp4"
    subtitles.write_srt(CUES, tmp_path / "clip.en.srt")
    w = make_worker({"REGENERATE": "always"})
    with pytest.raises(AttributeError):
        w._source_cues(video, "en", {})


def test_reuse_is_on_by_default():
    assert config.from_env({}).run.reuse_transcript is True
