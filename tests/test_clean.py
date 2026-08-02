from types import SimpleNamespace

import stash_subs as s


def seg(start, end, text, no_speech_prob=0.0, compression_ratio=1.0):
    return SimpleNamespace(
        start=start, end=end, text=text,
        no_speech_prob=no_speech_prob, compression_ratio=compression_ratio,
    )


def texts(cues):
    return [c[2] for c in cues]


def test_keeps_ordinary_speech():
    out = s.clean([seg(0, 1, "hello"), seg(1, 2, "there")])
    assert texts(out) == ["hello", "there"]


def test_strips_whitespace_and_drops_empty():
    out = s.clean([seg(0, 1, "  padded  "), seg(1, 2, "   "), seg(2, 3, "")])
    assert texts(out) == ["padded"]


def test_drops_high_no_speech_probability():
    out = s.clean([seg(0, 1, "kept", no_speech_prob=0.85),
                   seg(1, 2, "dropped", no_speech_prob=0.86)])
    assert texts(out) == ["kept"]


def test_drops_absurd_compression_ratio():
    out = s.clean([seg(0, 1, "kept", compression_ratio=2.6),
                   seg(1, 2, "dropped", compression_ratio=2.61)])
    assert texts(out) == ["kept"]


def test_drops_known_junk_phrases():
    junk = [
        "Subtitles by the Amara.org community",
        "Subs by someone",
        "Thanks for watching!",
        "Thank you for watching",
        "Please subscribe",
        "www.example.com",
        "http://example.com",
        "[Music]",
        "Subtítulos por alguien",
        "¡Gracias por ver!",
    ]
    out = s.clean([seg(i, i + 1, t) for i, t in enumerate(junk)])
    assert out == [], f"survived the junk filter: {texts(out)}"


def test_junk_filter_is_anchored_and_does_not_eat_real_dialogue():
    keep = ["I was thanking her for watching the kids",
            "The music stopped"]
    out = s.clean([seg(i, i + 1, t) for i, t in enumerate(keep)])
    assert texts(out) == keep


def test_repeat_loop_is_truncated():
    # Same line four times running is a decoder loop; two survive.
    out = s.clean([seg(i, i + 1, "over and over") for i in range(4)])
    assert len(out) == 2


def test_repeat_counter_resets_on_a_different_line():
    out = s.clean([seg(0, 1, "a"), seg(1, 2, "a"), seg(2, 3, "b"),
                   seg(3, 4, "a"), seg(4, 5, "a")])
    assert texts(out) == ["a", "a", "b", "a", "a"]


def test_repeat_detection_ignores_punctuation_and_case():
    out = s.clean([seg(0, 1, "Stop!"), seg(1, 2, "stop"),
                   seg(2, 3, "STOP..."), seg(3, 4, "stop?")])
    assert len(out) == 2


def test_timings_are_preserved():
    out = s.clean([seg(1.25, 3.5, "x")])
    assert out == [(1.25, 3.5, "x")]


def test_progress_is_reported_for_dropped_segments_too(monkeypatch):
    # The progress bar must not stall through a long run of filtered junk.
    seen = []
    monkeypatch.setattr(s, "set_state", lambda **kw: seen.append(kw.get("position")))
    s.clean([seg(0, 1, "Thanks for watching"), seg(1, 2, "real line")],
            report_progress=True)
    assert seen == [1, 2]


def test_accepts_a_generator_and_reports_progress_as_it_goes(monkeypatch):
    # faster-whisper yields segments lazily and clean() is what turns that
    # into the progress bar, so it must take an iterator, not a sequence,
    # and emit progress interleaved with consumption rather than at the end.
    events = []
    monkeypatch.setattr(s, "set_state", lambda **kw: events.append(("progress", kw.get("position"))))

    def stream():
        for i in range(3):
            events.append(("yield", i))
            yield seg(i, i + 1, f"line {i}")

    result = s.clean(stream(), report_progress=True)
    assert len(result) == 3
    assert events == [
        ("yield", 0), ("progress", 1),
        ("yield", 1), ("progress", 2),
        ("yield", 2), ("progress", 3),
    ]
