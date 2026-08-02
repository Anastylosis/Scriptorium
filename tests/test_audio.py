"""Decoding, against real media files synthesised with PyAV."""

import numpy as np
import pytest

from stash_subs import audio

av = pytest.importorskip("av")


def make_media(path, seconds=8.0, rate=44100, layout="stereo", codec=None,
               freq=440.0):
    """A file with a sine tone whose frequency rises each second, so a decoded
    window can be identified by what it contains."""
    codec = codec or ("pcm_s16le" if str(path).endswith(".wav") else "aac")
    container = av.open(str(path), mode="w")
    stream = container.add_stream(codec, rate=rate)
    stream.layout = layout
    n_ch = len(stream.layout.channels)

    total = int(seconds * rate)
    t = np.arange(total, dtype=np.float32) / rate
    # Frequency steps every second: second k is at freq * (k + 1).
    steps = (t.astype(np.int32) + 1).astype(np.float32)
    wave = 0.4 * np.sin(2 * np.pi * freq * steps * t)
    pcm = (wave * 32767).astype(np.int16)
    # Packed s16 wants L,R,L,R. Stacking on axis 0 and flattening would give
    # LLLL...RRRR, which decodes as the tone at double speed played twice --
    # so two different offsets would hold identical audio and a seeking bug
    # would pass unnoticed.
    interleaved = np.stack([pcm] * n_ch, axis=1).reshape(1, -1)

    frame = av.AudioFrame.from_ndarray(interleaved, format="s16", layout=layout)
    frame.rate = rate
    for packet in stream.encode(frame):
        container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return path


@pytest.fixture
def wav(tmp_path):
    return make_media(tmp_path / "tone.wav", seconds=8.0)


def dominant_hz(samples, rate=audio.SAMPLE_RATE):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return float(np.fft.rfftfreq(len(samples), 1 / rate)[int(np.argmax(spectrum))])


# -- shape and format ------------------------------------------------------

def test_decode_returns_mono_float32_at_16k(wav):
    got = audio.decode_window(wav, 0.0, 2.0)
    assert got.dtype == np.float32
    assert got.ndim == 1
    assert abs(len(got) - 2 * audio.SAMPLE_RATE) < audio.SAMPLE_RATE * 0.1


def test_decode_never_returns_more_than_asked_for(wav):
    assert len(audio.decode_window(wav, 0.0, 1.0)) <= audio.SAMPLE_RATE


def test_asking_past_the_end_yields_little_or_nothing(wav):
    got = audio.decode_window(wav, 30.0, 2.0)
    assert len(got) < audio.SAMPLE_RATE


def test_samples_are_in_range(wav):
    got = audio.decode_window(wav, 0.0, 2.0)
    assert got.size and np.abs(got).max() <= 1.0


def test_stereo_is_downmixed_to_one_channel(tmp_path):
    got = audio.decode_window(make_media(tmp_path / "s.wav", layout="stereo"), 0.0, 1.0)
    assert got.ndim == 1


def test_mono_input_works_too(tmp_path):
    got = audio.decode_window(make_media(tmp_path / "m.wav", layout="mono"), 0.0, 1.0)
    assert got.ndim == 1


def test_a_compressed_container_decodes(tmp_path):
    # Real libraries are not wav; this exercises an actual codec.
    got = audio.decode_window(make_media(tmp_path / "a.m4a", codec="aac"), 0.0, 2.0)
    assert got.size > audio.SAMPLE_RATE


# -- seeking to the right place -------------------------------------------

def test_seeking_lands_on_the_requested_audio(wav):
    # The tone steps every second, so the content identifies the offset.
    first = dominant_hz(audio.decode_window(wav, 0.0, 0.9))
    sixth = dominant_hz(audio.decode_window(wav, 6.0, 0.9))
    assert sixth > first * 2, f"expected a much higher tone at 6s ({first} -> {sixth})"


def test_two_different_offsets_give_different_audio(wav):
    a = audio.decode_window(wav, 1.0, 0.8)
    b = audio.decode_window(wav, 5.0, 0.8)
    n = min(len(a), len(b))
    assert n and not np.allclose(a[:n], b[:n])


# -- duration --------------------------------------------------------------

def test_probe_duration(wav):
    assert abs(audio.probe_duration(wav) - 8.0) < 0.5


def test_probe_duration_of_a_missing_file_is_zero(tmp_path):
    assert audio.probe_duration(tmp_path / "nope.mp4") == 0.0


def test_probe_duration_of_a_non_media_file_is_zero(tmp_path):
    junk = tmp_path / "notmedia.mp4"
    junk.write_bytes(b"this is not a video")
    assert audio.probe_duration(junk) == 0.0


# -- the language sample ---------------------------------------------------

def test_a_short_file_is_sampled_from_the_start(tmp_path):
    path = make_media(tmp_path / "short.wav", seconds=8.0)
    got = audio.language_sample(path, duration=8.0, seconds=2.0)
    assert abs(len(got) - 2 * audio.SAMPLE_RATE) < audio.SAMPLE_RATE * 0.2


def test_a_long_file_is_sampled_at_three_points(tmp_path):
    # Intros, music and silence at the start fool language detection, so a
    # long file must be sampled from the middle, not the first minute.
    path = make_media(tmp_path / "long.wav", seconds=360.0)
    got = audio.language_sample(path, duration=360.0, seconds=5.0)
    assert abs(len(got) - 15 * audio.SAMPLE_RATE) < audio.SAMPLE_RATE * 0.5


def test_the_three_windows_are_actually_different(tmp_path):
    path = make_media(tmp_path / "long.wav", seconds=360.0)
    got = audio.language_sample(path, duration=360.0, seconds=5.0)
    third = len(got) // 3
    assert not np.allclose(got[:third], got[third:2 * third])


def test_sampling_an_unreadable_file_raises_rather_than_returning_silence(tmp_path):
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not media")
    with pytest.raises(audio.AudioError):
        audio.language_sample(junk, duration=100.0)


def test_no_ffmpeg_binary_is_required(monkeypatch, wav):
    # The image no longer ships one; decoding must not depend on it.
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    assert audio.decode_window(wav, 0.0, 1.0).size
    assert audio.probe_duration(wav) > 0
