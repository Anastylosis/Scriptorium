import stash_subs as s


def test_ts_golden():
    assert s.ts(0.0) == "00:00:00,000"
    assert s.ts(1.5) == "00:00:01,500"
    assert s.ts(61.25) == "00:01:01,250"
    assert s.ts(3661.007) == "01:01:01,007"


def test_ts_rounds_up_into_the_next_hour():
    assert s.ts(3599.9995) == "01:00:00,000"


def test_ts_clamps_negative():
    # divmod on a negative millisecond count yields "-1:59:59,600", which is
    # not a timestamp any player will parse.
    assert s.ts(-0.4) == "00:00:00,000"
    assert s.ts(-1000.0) == "00:00:00,000"


def test_write_srt_structure(tmp_path):
    dest = tmp_path / "clip.en.srt"
    s.write_srt([(0.0, 1.0, "one"), (1.0, 2.5, "two")], dest)
    assert dest.read_text(encoding="utf-8") == (
        "1\n00:00:00,000 --> 00:00:01,000\none\n\n"
        "2\n00:00:01,000 --> 00:00:02,500\ntwo\n\n"
    )


def test_write_srt_is_atomic(tmp_path):
    dest = tmp_path / "clip.en.srt"
    s.write_srt([(0.0, 1.0, "x")], dest)
    assert dest.exists()
    assert list(tmp_path.iterdir()) == [dest], "the .part file must not survive"


def test_write_srt_overwrites_in_place(tmp_path):
    dest = tmp_path / "clip.en.srt"
    s.write_srt([(0.0, 1.0, "first")], dest)
    s.write_srt([(0.0, 1.0, "second")], dest)
    assert "second" in dest.read_text(encoding="utf-8")
    assert "first" not in dest.read_text(encoding="utf-8")


def test_blank_line_in_cue_text_does_not_split_the_cue(tmp_path):
    # A blank line terminates a cue in SRT, so text containing one would
    # silently turn a single cue into a malformed pair.
    dest = tmp_path / "clip.en.srt"
    s.write_srt([(0.0, 1.0, "line one\n\nline two"), (1.0, 2.0, "next")], dest)
    body = dest.read_text(encoding="utf-8")
    blocks = [b for b in body.split("\n\n") if b.strip()]
    assert len(blocks) == 2, f"expected 2 cues, got {len(blocks)}: {blocks!r}"
    assert blocks[1].startswith("2\n")


def test_crlf_in_cue_text_is_normalised(tmp_path):
    dest = tmp_path / "clip.en.srt"
    s.write_srt([(0.0, 1.0, "a\r\nb")], dest)
    assert "\r" not in dest.read_text(encoding="utf-8")
