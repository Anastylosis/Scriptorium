#!/usr/bin/env python3
"""Regenerate the provenance conformance fixtures.

    python3 spec/provenance/generate.py

The fixtures are produced by Scriptorium's own renderer, never written by
hand: a hand-made approximation tests consumers against a format nobody
actually emits.

Fixtures are APPEND-ONLY. Files carrying an old format exist in the wild
forever, so an old case must keep passing forever. Add a new fixture rather
than editing an existing one; if this script's output for an existing fixture
changes, that is a wire-format change and every consumer needs updating in
the same release.

See SPEC.md for the contract itself, and README.md for who consumes it.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from scriptorium.subtitles import (
    _SNIFF_BYTES,
    MARKER,
    OLD_MARKER,
    Provenance,
    render_vtt,
    with_annotation,
)

OUT = pathlib.Path(__file__).parent / "fixtures"

# Fixed values throughout: a fixture that changes when the clock does is not a
# fixture. Nothing here reads the current date or a real media file.
DATE = "2026-08-07"


def cues(n, start=0.0):
    return [(start + i * 5.0, start + i * 5.0 + 4.0, f"Line {i + 1}") for i in range(n)]


def build(prov, n=3, **extra):
    """What worker.py produces: annotated cues plus a NOTE holding as_json."""
    annotated = with_annotation(cues(n), prov, media_duration=n * 5.0)
    return render_vtt(annotated, note=prov.as_json(**extra))


def main():
    OUT.mkdir(parents=True, exist_ok=True)

    transcript = Provenance(version="1.4.0", asr_model="large-v3",
                            src="en", src_name="English",
                            dst="en", dst_name="English", date=DATE)

    # A plain transcript. Note as_dict emits mt_model as JSON null rather than
    # omitting it — consumers must treat null as "not translated".
    (OUT / "transcript.vtt").write_text(
        build(transcript, cues=3, media="oshash:0123456789abcdef"))

    # A machine translation of a machine transcription. mt_model set, and
    # src != dst. Materially worse than either alone, so consumers surface it
    # differently.
    translation = Provenance(version="1.4.0", asr_model="large-v3",
                             mt_model="gpt-4o-mini",
                             src="es", src_name="Spanish",
                             dst="en", dst_name="English", date=DATE)
    (OUT / "translation.vtt").write_text(build(translation, cues=3))

    # Output from before the stash-subs → Scriptorium rename. Still in the
    # wild, so still detected.
    (OUT / "legacy-marker.vtt").write_text(
        build(transcript).replace(MARKER, OLD_MARKER))

    # Hand-made subtitles. A false positive here mislabels someone's own work
    # as machine output, which is the costlier direction to get wrong.
    (OUT / "human.vtt").write_text(render_vtt(cues(3)))

    # Detection sniffs the first and last _SNIFF_BYTES. On a long file the
    # annotation cue sits far past the head window, so only the tail sniff
    # finds it.
    long_file = render_vtt(with_annotation(cues(400), transcript,
                                           media_duration=2000.0),
                           note=transcript.as_json())
    assert len(long_file) > 2 * _SNIFF_BYTES, "fixture must exceed both windows"
    (OUT / "marker-in-tail.vtt").write_text(long_file)

    # The documented blind spot: a marker in neither window is not found.
    # Sniffing the whole file would cost a full read per upload. Pinned so
    # that changing the strategy is a decision rather than an accident.
    middle = render_vtt(with_annotation(cues(200), transcript,
                                        media_duration=1000.0))
    buried = (render_vtt(cues(200))
              + middle.split("WEBVTT\n\n", 1)[1]
              + render_vtt(cues(200, start=2000.0)).split("WEBVTT\n\n", 1)[1])
    assert len(buried) > 2 * _SNIFF_BYTES
    (OUT / "marker-buried.vtt").write_text(buried)

    for f in sorted(OUT.iterdir()):
        print(f"{f.name:24} {f.stat().st_size:7} bytes")


if __name__ == "__main__":
    main()
