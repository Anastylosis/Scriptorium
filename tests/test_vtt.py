import json

import pytest

from scriptorium import config
from scriptorium.subtitles import (
    MARKER,
    Path,
    Provenance,
    dest_for,
    looks_generated,
    render,
    render_srt,
    render_vtt,
    sidecar_for,
    with_annotation,
)

CUES = [(1.0, 3.25, "first"), (10.0, 12.0, "second")]
PROV = Provenance(version="1.0.0", asr_model="large-v3-turbo", src="es",
                  src_name="Spanish", dst="en", dst_name="English",
                  date="2026-08-02")


def test_vtt_starts_with_the_required_header():
    assert render_vtt(CUES).startswith("WEBVTT\n\n")


def test_vtt_uses_a_dot_for_milliseconds():
    # SRT uses a comma; a VTT with commas will not parse.
    body = render_vtt(CUES)
    assert "00:00:01.000 --> 00:00:03.250" in body
    assert "," not in body.split("WEBVTT")[1].split("first")[0]


def test_srt_still_uses_a_comma():
    assert "00:00:01,000 --> 00:00:03,250" in render_srt(CUES)


def test_the_note_block_carries_parseable_provenance():
    body = render_vtt(CUES, note=PROV.as_json(cues=2))
    block = body.split("NOTE\n")[1].split("\n\n")[0]
    data = json.loads(block)
    assert data["tool"] == "scriptorium"
    assert data["asr_model"] == "large-v3-turbo"
    assert data["src"] == "es" and data["dst"] == "en"
    assert data["cues"] == 2


def test_a_note_never_contains_a_blank_line():
    # A blank line ends the NOTE block, so the rest would be read as cues.
    body = render_vtt(CUES, note="first\n\n\nsecond")
    note = body.split("NOTE\n")[1].split("\n\n")[0]
    assert "\n\n" not in note
    assert body.count("-->") == 2


def test_without_a_note_there_is_no_note_block():
    assert "NOTE" not in render_vtt(CUES)


def test_the_visible_marker_is_still_a_cue_in_vtt():
    # The NOTE is invisible; the point of the marker is that a viewer sees it.
    body = render_vtt(with_annotation(CUES, PROV, media_duration=20.0))
    assert MARKER in body
    assert body.count("-->") == 3


def test_looks_generated_recognises_a_vtt(tmp_path):
    f = tmp_path / "clip.en.vtt"
    f.write_text(render_vtt(with_annotation(CUES, PROV, media_duration=20.0)),
                 encoding="utf-8")
    assert looks_generated(f)


def test_render_dispatches_on_format():
    assert render(CUES, "vtt").startswith("WEBVTT")
    assert render(CUES, "srt").startswith("1\n")


def test_dest_extension_follows_the_format():
    video = Path("/data/clip.mp4")
    assert dest_for(video, "en", "srt").name == "clip.en.srt"
    assert dest_for(video, "en", "vtt").name == "clip.en.vtt"


def test_sidecar_sits_beside_the_subtitle():
    assert sidecar_for(Path("/data/clip.en.srt")).name == "clip.en.srt.scriptorium.json"


# -- config ----------------------------------------------------------------

def test_srt_only_by_default():
    assert config.from_env({}).output.formats == ["srt"]
    assert config.from_env({}).annotate.sidecar is False


def test_both_formats_can_be_requested():
    assert config.from_env({"OUTPUT_FORMATS": "srt,vtt"}).output.formats == ["srt", "vtt"]


def test_vtt_only():
    assert config.from_env({"OUTPUT_FORMATS": "vtt"}).output.formats == ["vtt"]


@pytest.mark.parametrize("value", ["ass", "srt,ass", ""])
def test_an_unsupported_format_is_refused_at_startup(value):
    # Stash reads only srt and vtt; anything else would be written and never
    # attach to the scene.
    with pytest.raises(config.ConfigError, match="OUTPUT_FORMATS"):
        config.from_env({"OUTPUT_FORMATS": value or ","})


def test_sidecar_can_be_enabled():
    assert config.from_env({"ANNOTATE_SIDECAR": "1"}).annotate.sidecar is True


def test_provenance_json_is_stable_and_sorted():
    a = Provenance(version="1", asr_model="m", date="d").as_json()
    b = Provenance(version="1", asr_model="m", date="d").as_json()
    assert a == b
    assert list(json.loads(a)) == sorted(json.loads(a))


def test_no_translation_model_is_null_not_empty_string():
    assert json.loads(PROV.as_json())["mt_model"] is None
