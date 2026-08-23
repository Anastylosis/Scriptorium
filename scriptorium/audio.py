"""Reading audio out of a media file.

Decoding goes through PyAV, which faster-whisper already depends on, so the
FFmpeg libraries are in the process either way and no `ffmpeg` binary has to
be installed alongside. Samples are handed to Whisper as arrays rather than
written to temporary wav files.

If PyAV cannot open something and an `ffmpeg` binary happens to be on PATH,
that is used instead. The image does not ship one; a derived image can.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import wave

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# What to fall back to when the usual three-point sample cannot be taken.
# Bounded on purpose: handing a whole file to Whisper just to identify the
# language costs minutes on a long scene, because the log-mel is computed
# eagerly before any segment comes back.
FALLBACK_SECONDS = 120.0

_warned_ffmpeg = False


class AudioError(RuntimeError):
    pass


def _ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def probe_duration(path) -> float:
    """Length in seconds, or 0.0 if it cannot be determined."""
    try:
        import av

        with av.open(str(path)) as container:
            if container.duration is not None:
                return container.duration / av.time_base
            for stream in container.streams.audio:
                if stream.duration is not None and stream.time_base:
                    return float(stream.duration * stream.time_base)
    except Exception as e:
        log.debug("PyAV could not read the duration of %s: %s", path, e)
        if _ffmpeg_available():
            return _ffprobe_duration(path)
    return 0.0


def decode_window(path, start: float, seconds: float) -> np.ndarray:
    """`seconds` of 16 kHz mono float32 starting at `start`."""
    try:
        return _decode_pyav(path, start, seconds)
    except Exception as e:
        if not _ffmpeg_available():
            raise AudioError(f"could not decode {path}: {e}") from None
        global _warned_ffmpeg
        if not _warned_ffmpeg:
            _warned_ffmpeg = True
            log.info("PyAV could not decode %s (%s); falling back to ffmpeg", path, e)
        return _decode_ffmpeg(path, start, seconds)


def language_sample(path, duration: float, seconds: float = 45.0) -> np.ndarray:
    """Audio for language detection.

    Three windows from 25/50/75% of a long file rather than the opening
    minute: intros, music and silence at the start fool Whisper's built-in
    detection constantly.
    """
    points = [duration * f for f in (0.25, 0.5, 0.75)] if duration > 300 else [0.0]
    chunks = [c for c in (decode_window(path, p, seconds) for p in points) if c.size]
    if not chunks:
        raise AudioError(f"no audio decoded from {path}")
    return np.concatenate(chunks)


# -- PyAV ------------------------------------------------------------------

def _decode_pyav(path, start, seconds):
    import av

    want = int(seconds * SAMPLE_RATE)
    out = []
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise AudioError("no audio stream")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        if start > 0 and stream.time_base:
            container.seek(int(start / stream.time_base), stream=stream)
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        taken = 0
        for frame in container.decode(stream):
            # Seeking lands on a keyframe, which may be before the window.
            if (frame.pts is not None and stream.time_base
                    and float(frame.pts * stream.time_base) < start - 1.0):
                continue
            for chunk in _resample(resampler, frame):
                out.append(chunk)
                taken += chunk.size
            if taken >= want:
                break
        out.extend(_resample(resampler, None))
    if not out:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(out)[:want].astype(np.float32, copy=False)


def _resample(resampler, frame):
    frames = resampler.resample(frame)
    if frames is None:
        return []
    if not isinstance(frames, (list, tuple)):
        frames = [frames]
    # to_ndarray() is a view onto the AVFrame's buffer, which is freed once
    # the frame goes out of scope here. Without the copy the collected
    # chunks all end up reading whatever later allocations left in that
    # memory, and the decoded audio is silently wrong rather than absent.
    return [f.to_ndarray().reshape(-1).copy() for f in frames]


# -- ffmpeg fallback, only if a binary is present ---------------------------

def _ffprobe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _decode_ffmpeg(path, start, seconds):
    with tempfile.TemporaryDirectory() as td:
        dest = os.path.join(td, "chunk.wav")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(start), "-t", str(seconds),
             "-i", str(path), "-ac", "1", "-ar", str(SAMPLE_RATE), "-vn",
             "-f", "wav", dest],
            check=True, timeout=600,
        )
        with wave.open(dest, "rb") as w:
            raw = w.readframes(w.getnframes())
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return pcm
