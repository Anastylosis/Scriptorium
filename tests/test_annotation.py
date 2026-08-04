import pytest

from scriptorium import config
from scriptorium.subtitles import (
    DEFAULT_TEMPLATE,
    MARKER,
    MAX_OVERSHOOT,
    OLD_MARKER,
    Provenance,
    TemplateError,
    annotation_cue,
    looks_generated,
    render_srt,
    should_write,
    validate_template,
    with_annotation,
)

CUES = [(1.0, 3.0, "first line"), (10.0, 12.0, "last line")]
PROV = Provenance(version="1.2.3", asr_model="large-v3-turbo", src="es",
                  src_name="Spanish", dst="en", dst_name="English",
                  date="2026-08-02")


def test_default_template_renders_everything():
    text = DEFAULT_TEMPLATE.format_map(PROV.format_map())
    assert text.startswith(MARKER)
    assert "large-v3-turbo" in text
    assert "Spanish → English" in text
    assert "2026-08-02" in text


def test_a_transcript_names_one_language_not_two():
    # "English → English" reads like a mistake.
    prov = Provenance(asr_model="large-v3-turbo", src="en", src_name="English",
                      dst="en", dst_name="English", date="2026-08-02")
    text = DEFAULT_TEMPLATE.format_map(prov.format_map())
    assert "English → English" not in text
    assert "· English ·" in text


def test_translation_model_appears_only_when_one_was_used():
    without = DEFAULT_TEMPLATE.format_map(PROV.format_map())
    assert " + " not in without
    with_mt = DEFAULT_TEMPLATE.format_map(
        Provenance(**{**PROV.__dict__, "mt_model": "translategemma:4b"}).format_map())
    assert "+ translategemma:4b" in with_mt


# -- placement -------------------------------------------------------------

def test_end_is_the_default_and_goes_last():
    out = with_annotation(CUES, PROV, media_duration=20.0)
    assert len(out) == 3
    assert MARKER in out[-1][2]
    assert out[:2] == CUES


def test_end_starts_after_the_last_real_cue():
    cue = annotation_cue(CUES, PROV, mode="end", gap=1.0, media_duration=20.0)
    assert cue[0] >= CUES[-1][1]


def test_a_long_marker_is_reined_in():
    # Runtime-based subtitle matching tolerates roughly twenty seconds before
    # it decides a subtitle belongs to a different video.
    cue = annotation_cue(CUES, PROV, mode="end", seconds=300.0, gap=5.0,
                         media_duration=13.0)
    assert cue[1] <= 13.0 + MAX_OVERSHOOT


def test_the_marker_never_covers_dialogue_or_runs_out_of_order():
    # Speech running to the very end of a short clip used to push the marker
    # backwards on top of the dialogue, producing cues out of chronological
    # order. Overshooting the media slightly is the lesser evil and is well
    # inside what runtime matching tolerates.
    for duration in (11.0, 12.0, 12.1, 13.0, 15.0, 60.0):
        cue = annotation_cue(CUES, PROV, mode="end", media_duration=duration)
        assert cue[0] >= CUES[-1][1], f"overlaps dialogue at {duration}"
        assert cue[1] > cue[0], duration
        assert cue[1] <= duration + MAX_OVERSHOOT, duration


def test_speech_to_the_last_frame_still_gets_a_clean_marker():
    cues = [(0.02, 11.0, "talking right up to the end")]
    out = with_annotation(cues, PROV, media_duration=11.0)
    assert len(out) == 2
    assert out[1][0] >= out[0][1], "the marker must start after the dialogue"


def test_end_without_a_known_duration_still_produces_a_sane_cue():
    cue = annotation_cue(CUES, PROV, mode="end", media_duration=0.0)
    assert cue[0] > CUES[-1][1]
    assert cue[1] > cue[0]


def test_start_goes_first():
    cues = [(10.0, 12.0, "much later")]
    out = with_annotation(cues, PROV, mode="start", media_duration=20.0)
    assert MARKER in out[0][2]
    assert out[1:] == cues


def test_start_is_skipped_when_dialogue_begins_immediately():
    # Better no marker than one covering the opening line.
    cues = [(0.4, 2.0, "hello")]
    assert annotation_cue(cues, PROV, mode="start", seconds=3.0) is None
    assert with_annotation(cues, PROV, mode="start") == cues


def test_none_produces_byte_identical_output():
    plain = render_srt(CUES)
    annotated = render_srt(with_annotation(CUES, PROV, mode="none"))
    assert plain == annotated


def test_no_cues_means_no_annotation():
    assert annotation_cue([], PROV, mode="end") is None
    assert with_annotation([], PROV, mode="end") == []


def test_the_annotation_survives_rendering():
    body = render_srt(with_annotation(CUES, PROV, media_duration=20.0))
    assert MARKER in body
    assert body.count("-->") == 3


# -- template validation ---------------------------------------------------

def test_a_good_template_validates():
    validate_template("{marker} made by {tool} {version}")


def test_an_unknown_placeholder_is_rejected():
    with pytest.raises(TemplateError, match="unknown placeholder"):
        validate_template("{marker} {nonsense}")


def test_a_template_without_the_marker_is_rejected():
    # Without the sentinel a later run cannot recognise its own output and
    # if-ours would overwrite hand-made subtitles.
    with pytest.raises(TemplateError, match="must include"):
        validate_template("machine-generated by {tool}")


def test_a_bad_template_fails_at_config_load_not_mid_transcription():
    with pytest.raises(config.ConfigError, match="ANNOTATE_TEXT"):
        config.from_env({"ANNOTATE_TEXT": "{marker} {bogus}"})


def test_an_unknown_annotate_mode_is_rejected():
    with pytest.raises(config.ConfigError, match="ANNOTATE"):
        config.from_env({"ANNOTATE": "middle"})


@pytest.mark.parametrize("mode", ["none", "start", "end"])
def test_valid_modes_load(mode):
    assert config.from_env({"ANNOTATE": mode}).annotate.mode == mode


# -- recognising our own output -------------------------------------------

def test_looks_generated_round_trips(tmp_path):
    f = tmp_path / "a.en.srt"
    f.write_text(render_srt(with_annotation(CUES, PROV, media_duration=20.0)),
                 encoding="utf-8")
    assert looks_generated(f)


def test_looks_generated_finds_a_start_marker(tmp_path):
    cues = [(10.0, 12.0, "later")]
    f = tmp_path / "a.en.srt"
    f.write_text(render_srt(with_annotation(cues, PROV, mode="start")),
                 encoding="utf-8")
    assert looks_generated(f)


def test_a_hand_made_subtitle_is_not_ours(tmp_path):
    f = tmp_path / "a.en.srt"
    f.write_text("1\n00:00:01,000 --> 00:00:03,000\nhello\n\n", encoding="utf-8")
    assert not looks_generated(f)


def test_the_marker_is_found_in_a_large_file(tmp_path):
    filler = [(float(i), float(i) + 0.5, f"line {i}") for i in range(4000)]
    f = tmp_path / "big.en.srt"
    f.write_text(render_srt(with_annotation(filler, PROV, media_duration=5000.0)),
                 encoding="utf-8")
    assert f.stat().st_size > 100_000
    assert looks_generated(f)


def test_looks_generated_on_a_missing_file_is_false(tmp_path):
    assert not looks_generated(tmp_path / "nope.srt")


def test_looks_generated_still_recognises_the_pre_rename_marker(tmp_path):
    # Files written by this project under its old name, stash-subs, are
    # still out there and must be treated as ours forever.
    f = tmp_path / "a.en.srt"
    f.write_text(render_srt([(1.0, 3.0, f"{OLD_MARKER} machine-generated")]),
                encoding="utf-8")
    assert looks_generated(f)


# -- regenerate ------------------------------------------------------------

def test_never_leaves_an_existing_file_alone(tmp_path):
    f = tmp_path / "a.en.srt"
    f.write_text("x", encoding="utf-8")
    assert not should_write(f, "never")


def test_a_missing_file_is_always_written(tmp_path):
    assert should_write(tmp_path / "a.en.srt", "never")


def test_always_overwrites_anything(tmp_path):
    f = tmp_path / "a.en.srt"
    f.write_text("hand made", encoding="utf-8")
    assert should_write(f, "always")


def test_if_ours_overwrites_our_output_but_not_a_hand_made_file(tmp_path):
    ours = tmp_path / "ours.en.srt"
    ours.write_text(render_srt(with_annotation(CUES, PROV, media_duration=20.0)),
                    encoding="utf-8")
    theirs = tmp_path / "theirs.en.srt"
    theirs.write_text("1\n00:00:01,000 --> 00:00:03,000\nhello\n\n", encoding="utf-8")
    assert should_write(ours, "if-ours")
    assert not should_write(theirs, "if-ours")


def test_if_ours_also_overwrites_pre_rename_output(tmp_path):
    old = tmp_path / "old.en.srt"
    old.write_text(render_srt([(1.0, 3.0, f"{OLD_MARKER} machine-generated")]),
                   encoding="utf-8")
    assert should_write(old, "if-ours")


def test_overwrite_env_still_means_always():
    assert config.from_env({"OVERWRITE": "1"}).run.regenerate == "always"
    assert config.from_env({"OVERWRITE": "0"}).run.regenerate == "never"


def test_regenerate_overrides_overwrite():
    cfg = config.from_env({"OVERWRITE": "1", "REGENERATE": "if-ours"})
    assert cfg.run.regenerate == "if-ours"


def test_an_unknown_regenerate_mode_is_rejected():
    with pytest.raises(config.ConfigError, match="REGENERATE"):
        config.from_env({"REGENERATE": "sometimes"})
