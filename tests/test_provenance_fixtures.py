"""The provenance fixtures must keep matching what this code emits.

spec/provenance/fixtures/ is consumed by moansubs, in Go, with nothing at
build time keeping the two languages in agreement. Detection there fails open:
if the format moves and the consumer is not updated, machine transcripts are
ingested with no disclosure — no error, just a missing flag.

So this asserts the committed fixtures are exactly what the current renderer
produces. A failure here is not a broken test; it means the wire format
changed and every consumer needs updating in the same release. See
spec/provenance/SPEC.md.
"""

import importlib.util
import pathlib
import sys

import pytest

SPEC = pathlib.Path(__file__).resolve().parents[1] / "spec" / "provenance"
FIXTURES = SPEC / "fixtures"


def _generator():
    spec = importlib.util.spec_from_file_location("provgen", SPEC / "generate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["provgen"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_fixtures_match_current_renderer(tmp_path, monkeypatch):
    gen = _generator()
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.main()

    committed = sorted(p.name for p in FIXTURES.glob("*.vtt"))
    regenerated = sorted(p.name for p in tmp_path.glob("*.vtt"))
    assert regenerated == committed, (
        "the generator no longer produces the same set of fixtures; "
        "fixtures are append-only, so removing one breaks a consumer"
    )

    for name in committed:
        want = (FIXTURES / name).read_text()
        got = (tmp_path / name).read_text()
        assert got == want, (
            f"{name} no longer matches what the renderer emits.\n"
            "This is a wire-format change: regenerate with "
            "`python3 spec/provenance/generate.py`, and update every consumer "
            "in the same release (moansubs parses these)."
        )


def test_generator_is_deterministic(tmp_path, monkeypatch):
    """No clock, no randomness — otherwise the drift check cries wolf."""
    gen = _generator()
    first, second = tmp_path / "a", tmp_path / "b"
    for out in (first, second):
        monkeypatch.setattr(gen, "OUT", out)
        gen.main()

    for f in sorted(first.glob("*.vtt")):
        assert f.read_text() == (second / f.name).read_text(), f"{f.name} is not deterministic"


@pytest.mark.parametrize("name", ["transcript.vtt", "translation.vtt", "legacy-marker.vtt"])
def test_generated_fixtures_are_detected_by_our_own_reader(name):
    """Scriptorium must recognise its own output, or a re-run overwrites it."""
    from scriptorium.subtitles import looks_generated

    assert looks_generated(FIXTURES / name), f"{name} is not recognised as our own output"


def test_human_fixture_is_not_detected():
    """The costly direction: never mistake a person's work for ours."""
    from scriptorium.subtitles import looks_generated

    assert not looks_generated(FIXTURES / "human.vtt")
