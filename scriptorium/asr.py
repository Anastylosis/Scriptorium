"""Whisper transcription and the hallucination filter."""

import logging
import re

from . import audio

log = logging.getLogger(__name__)

# Segments matching these are almost always Whisper hallucinating on non-speech.
JUNK_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"^\s*subtitle[sd]?\s+by\b", r"^\s*subs?\s+by\b", r"\bamara\.org\b",
        r"^\s*thanks?\s+for\s+watching", r"^\s*thank\s+you\s+for\s+watching",
        r"^\s*please\s+subscribe", r"^\s*www\.", r"^\s*http", r"^\s*\[?music\]?\s*$",
        r"^\s*subtítulos\s+(por|realizados)", r"^\s*¡?\s*gracias\s+por\s+ver",
    ]
]


def whisper_translates(name: str) -> bool:
    """Whisper's turbo checkpoints were fine-tuned on transcription data with
    translation excluded. Asking them for task="translate" does not fail — it
    silently returns a transcript in the source language. So English output
    must not be routed through Whisper when running a turbo model."""
    return "turbo" not in name.lower()


def clean(segments, on_progress=None):
    """Drop hallucinations: junk phrases, silence artefacts, repeat loops.

    faster-whisper yields segments lazily, so consuming them here doubles as
    the progress signal: each segment's end timestamp is how far into the
    media we have decoded. Progress fires before filtering so the bar does
    not stall through a long run of junk.
    """
    out, prev, repeats = [], None, 0
    for s in segments:
        if on_progress is not None:
            on_progress(s.end)
        text = s.text.strip()
        if not text:
            continue
        if getattr(s, "no_speech_prob", 0) > 0.85:
            continue
        if getattr(s, "compression_ratio", 0) > 2.6:
            continue
        if any(p.search(text) for p in JUNK_PATTERNS):
            continue
        norm = re.sub(r"\W+", "", text.lower())
        if norm == prev:
            repeats += 1
            if repeats >= 2:      # same line 3x running = decoder loop
                continue
        else:
            prev, repeats = norm, 0
        out.append((s.start, s.end, text))
    return out


class Models:
    """Lazily loaded, cached Whisper models.

    Not thread-safe: two concurrent loads would pull the model twice. There
    is exactly one worker thread by design.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self._cache = {}

    def get(self, name=None):
        name = name or self.cfg.name
        if name not in self._cache:
            # Imported here so the module graph stays stdlib-only until a
            # transcription actually starts.
            from faster_whisper import WhisperModel
            log.info("loading model %s (%s, %s threads)",
                     name, self.cfg.compute_type, self.cfg.threads)
            self._cache[name] = WhisperModel(
                name, device=self.cfg.device, compute_type=self.cfg.compute_type,
                cpu_threads=self.cfg.threads, download_root=self.cfg.directory,
            )
        return self._cache[name]

    def detect_language(self, path, duration):
        try:
            sample = audio.language_sample(path, duration)
        except Exception as e:
            # Bounded on purpose. Handing the whole file to transcribe() costs
            # minutes on a long scene, because faster-whisper computes the
            # full log-mel before yielding anything.
            log.info("  language sampling failed (%s); using the first %.0fs",
                     e, audio.FALLBACK_SECONDS)
            sample = audio.decode_window(path, 0.0, audio.FALLBACK_SECONDS)
        _, info = self.get().transcribe(sample, language=None, vad_filter=True,
                                        beam_size=1)
        return info.language, info.language_probability

    def transcribe(self, path, language, task="transcribe", model=None, on_progress=None):
        segments, info = self.get(model).transcribe(
            str(path),
            language=language,
            task=task,
            beam_size=self.cfg.beam_size,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
            condition_on_previous_text=False,   # stops repetition death-spirals
            no_speech_threshold=0.6,
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        )
        return clean(segments, on_progress=on_progress), info
