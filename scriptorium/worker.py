"""The queue loop: take what the library offers, produce subtitles, record
the outcome. Which library it is — Stash, or a watched folder — is `library`'s
business, not this module's."""

import logging
import threading
import time
from collections import Counter

from . import __version__, captions, langs, outcomes, subtitles, tags
from .asr import Models, whisper_translates
from .audio import probe_duration
from .library import open_library
from .translate import Ollama

log = logging.getLogger(__name__)

# A directory is asked for as soon as the queue holds no more scenes in it,
# which on a path-sorted queue is one job per directory. This is the backstop
# for the directory holding most of the queue, whose scenes are finished with
# long before it is: it caps how long a caption sits on disk unregistered, and
# with it how often Stash walks a directory still being written into.
#
# The cap has to be a duration. A count of directories never reaches a
# threshold, since a queue of thousands of scenes is usually a handful of
# directories, and a count of scenes means little when they differ in cost by
# two orders of magnitude.
SCAN_INTERVAL = 600


class Control:
    """Wakeup, pause and stop signalling for the single worker thread."""

    def __init__(self):
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._paused = threading.Event()

    @property
    def paused(self):
        return self._paused.is_set()

    @property
    def stopping(self):
        return self._stop.is_set()

    def request_poll(self):
        self._wake.set()
        if self._paused.is_set():
            return "paused — resume to process the queue"
        return "polling now"

    def pause(self):
        self._paused.set()
        self._wake.set()
        return "pausing after the current scene"

    def resume(self):
        self._paused.clear()
        self._wake.set()
        return "resumed"

    def request_stop(self):
        self._stop.set()
        self._wake.set()

    def sleep(self, seconds):
        """Wait up to `seconds`; returns True if woken early."""
        woke = self._wake.wait(seconds)
        self._wake.clear()
        return woke


class Worker:
    def __init__(self, cfg, store, control=None, client=None, library=None):
        self.cfg = cfg
        self.store = store
        self.control = control or Control()
        # Where the work comes from: a Stash library, or a watched folder.
        # Everything below process_scene is the same either way.
        self.library = library or open_library(cfg, client=client)
        self.models = Models(cfg.model)
        self.ollama = Ollama(cfg.ollama)
        # Destinations already produced for the scene in hand; see write().
        self._written = set()

    # -- setup ------------------------------------------------------------

    def bootstrap(self):
        self.library.bootstrap()
        self.publish_requests()

        if not whisper_translates(self.cfg.model.name):
            if self.cfg.model.translate_model:
                how = f"a second Whisper model ({self.cfg.model.translate_model})"
            elif self.cfg.ollama.url:
                how = f"the LLM ({self.cfg.ollama.model})"
            else:
                how = None
            if how:
                log.info("note: %s cannot translate; English output will use %s",
                         self.cfg.model.name, how)
            else:
                log.warning(
                    "WARNING: %s cannot translate and no fallback is configured. "
                    "Non-English audio asked for in English will be skipped. "
                    "Set OLLAMA_URL, or TRANSLATE_MODEL=large-v3.", self.cfg.model.name)

        # The model is loaded lazily, so a device the image cannot serve fails
        # forty minutes in, on the first scene, as an unreadable CUDA error
        # against a scene that then reads as failed. Say it on the way up.
        if self.cfg.model.device != "cpu":
            log.warning("WARNING: DEVICE=%s, but the published image ships CPU "
                        "wheels and no cuDNN — the model will most likely fail "
                        "to load on the first scene. CPU is the supported "
                        "configuration.", self.cfg.model.device)

        if self.cfg.ollama.url:
            if not self.ollama.ready():
                log.warning("WARNING: LLM translation unavailable — targets that "
                            "need it will be skipped, but transcription still works")
        else:
            log.info("OLLAMA_URL not set — only source-language and English "
                     "output are possible")

    def publish_requests(self):
        self.store.update(request_tags=self.library.requested(),
                          waiting_hint=self.library.waiting_hint())

    # -- per scene --------------------------------------------------------

    def _progress(self, position):
        self.store.update(position=position)

    def process_scene(self, scene):
        files = scene.get("files") or []
        if not files:
            log.info("scene %s: no file attached, skipping", scene["id"])
            return outcomes.failed("no file attached")

        local = self.library.to_local(scene)
        label = scene.get("title") or local.name
        log.info("scene %s: %s", scene["id"], label)

        if not local.exists():
            log.error("  ERROR path not visible to this container: %s", local)
            return outcomes.failed(f"path not visible: {local}")

        wanted = self.library.targets_for(scene)
        if not wanted:
            return outcomes.Scene()

        duration = files[0].get("duration") or probe_duration(local)
        self.store.update(status="working", scene=label, scene_id=scene["id"],
                          stage="detecting language", targets=wanted,
                          duration=duration, position=0.0,
                          started_scene=time.time(),
                          source_lang=None, lang_confidence=None)

        src, prob = self.models.detect_language(local, duration)
        log.info("  source language: %s (%.0f%% confident), %.0f min",
                 src, prob * 100, duration / 60)
        # `auto` is a request, not a language. Until the detector has run the
        # page can only name the request; after it, it should say what is
        # actually being produced, and the language in hand is matched against
        # this list to be marked.
        resolved = []
        for t in wanted:
            lang = src if t == tags.AUTO else t
            if lang not in resolved:
                resolved.append(lang)
        self.store.update(source_lang=src, lang_confidence=prob,
                          targets=resolved)

        cache = {}
        self._written = set()
        produced = tuple(self.produce(local, scene, src, t, cache, duration)
                         for t in wanted)
        result = outcomes.Scene(targets=produced,
                                wrote=tuple(sorted(self._written)))
        log.info("  %s", result.summary())
        return result

    def produce(self, local, scene, src, target, cache, duration):
        cfg = self.cfg
        lang = src if target == tags.AUTO else target

        # Belt and braces: a tag was validated before it got here, but the
        # `auto` target takes its language from Whisper's detector at runtime.
        if not langs.is_caption_suffix(lang):
            log.warning("  refusing to write .%s.srt: %s", lang,
                        langs.reject_reason(lang))
            return outcomes.Target(lang, outcomes.UNSUPPORTED,
                                   "not a caption language Stash can attach")

        formats = cfg.output.formats
        pending = [f for f in formats
                   if subtitles.should_write(subtitles.dest_for(local, lang, f),
                                             cfg.run.regenerate)]
        if not pending:
            why = ("not ours to overwrite" if cfg.run.regenerate == "if-ours"
                   else "exists")
            log.info("  %s.%s %s, skipping", lang, "/".join(formats), why)
            return outcomes.Target(lang, outcomes.SKIPPED, why)
        dest = subtitles.dest_for(local, lang, pending[0])

        # The library may already carry this language under another spelling
        # of the same code; writing ours as well would just add a duplicate
        # track. Our own destinations are excluded: `should_write` has already
        # ruled on those, and counting one of them as "covered" is how
        # regenerate=if-ours came to refuse to regenerate anything once the
        # caption was registered — in folder mode, where the caption list is
        # read off the disk we just wrote to, that would be always.
        if cfg.run.regenerate != "always":
            mine = {subtitles.dest_for(local, lang, f) for f in formats}
            covered = captions.existing_file(local, scene, lang)
            if covered is not None and covered not in mine:
                log.info("  %s already covers %s, skipping", covered.name, lang)
                return outcomes.Target(lang, outcomes.SKIPPED,
                                       f"covered by {covered.name}")

        self.store.update(target=lang, position=0.0)

        # Whisper can only translate INTO English, and turbo cannot translate
        # at all, so anything else has to go through the LLM.
        if lang == src:
            route = "transcribe"
        elif lang == "en" and whisper_translates(cfg.model.name):
            route = "whisper-translate"
        elif lang == "en" and cfg.model.translate_model:
            route = "whisper-translate-alt"
        else:
            route = "llm"

        if route == "transcribe":
            cues = self._transcribed(local, src, cache)
        elif route == "whisper-translate":
            self.store.update(stage=f"translating {src} → en (whisper)")
            cues, _ = self.models.transcribe(local, src, "translate",
                                             on_progress=self._progress)
        elif route == "whisper-translate-alt":
            self.store.update(stage=f"translating {src} → en ({cfg.model.translate_model})")
            cues, _ = self.models.transcribe(local, src, "translate",
                                             model=cfg.model.translate_model,
                                             on_progress=self._progress)
        else:
            cues, salvage_new, why = self._via_llm(local, scene, src, lang,
                                                   cache, duration)
            if cues is None:
                # The translation did not happen, but a salvaged transcript
                # in a language Stash has not seen still needs registering.
                return outcomes.Target(lang, why[0], why[1],
                                       new_caption=salvage_new)

        if not cues:
            log.info("  no speech found for %s, nothing written", lang)
            return outcomes.Target(lang, outcomes.NO_SPEECH)
        if cfg.run.dry_run:
            log.info("  [dry run] would write %s (%d cues)", dest.name, len(cues))
            return outcomes.Target(lang, outcomes.DRY_RUN, f"{len(cues)} cues")
        self.write(cues, local, src, lang, duration,
                   mt_model=cfg.ollama.model if route == "llm" else "")
        # Only a language Stash has not seen before needs a rescan; rewriting
        # a registered caption is served from disk.
        new = any(not captions.registered(scene, lang, ext=f)
                  for f in cfg.output.formats)
        return outcomes.Target(lang, outcomes.WRITTEN, f"{len(cues)} cues",
                               new_caption=new)

    def write(self, cues, local, src, lang, duration, mt_model=""):
        """Add the generation marker and write every requested format.

        The marker is applied here rather than upstream so it can never be
        handed to the translator: translate() only ever sees transcribed cues.

        A destination already written for this scene is left alone. The
        source-language transcript has two claims on it — the salvage write
        that guards the LLM call, and `subs:<src>` asked for as a target in
        its own right — and whichever runs second was rewriting the first
        one's bytes: same cached cues, same provenance (dated, not stamped),
        one more line in the log and one more row on the status page for a
        file that had not changed.
        """
        cfg = self.cfg
        prov = self.provenance(src, lang, mt_model)
        annotated = subtitles.with_annotation(
            cues, prov,
            mode=cfg.annotate.mode,
            seconds=cfg.annotate.seconds,
            gap=cfg.annotate.gap,
            media_duration=duration,
            template=cfg.annotate.text or subtitles.DEFAULT_TEMPLATE,
        )
        note = prov.as_json(cues=len(cues))
        for fmt in cfg.output.formats:
            dest = subtitles.dest_for(local, lang, fmt)
            if dest in self._written:
                continue
            subtitles.write_text(
                subtitles.render(annotated, fmt=fmt, note=note), dest)
            self._written.add(dest)
            log.info("  wrote %s (%d cues)", dest.name, len(cues))
            self.store.add_completed(f"{dest.name} — {len(cues)} cues")
            if cfg.annotate.sidecar:
                subtitles.write_text(prov.as_json(cues=len(cues),
                                                  media=str(local)),
                                     subtitles.sidecar_for(dest))

    def provenance(self, src, dst, mt_model=""):
        return subtitles.Provenance(
            version=__version__,
            asr_model=self.cfg.model.name,
            mt_model=mt_model,
            src=src, src_name=langs.name_of(src),
            dst=dst, dst_name=langs.name_of(dst),
            date=time.strftime("%Y-%m-%d"),
        )

    def _transcribed(self, local, src, cache):
        if src not in cache:
            self.store.update(stage=f"transcribing {src}")
            cache[src], _ = self.models.transcribe(local, src, "transcribe",
                                                   on_progress=self._progress)
        return cache[src]

    def _source_cues(self, local, src, cache):
        """Cues to translate from.

        An existing transcript in the source language is read rather than
        re-derived: the audio has already been through Whisper once and
        doing it again costs minutes per scene for the same text. A
        hand-made or downloaded transcript is a better source than a fresh
        machine one anyway.
        """
        if src in cache:
            return cache[src]
        # regenerate=always is a request to redo the work, not to recycle it.
        if self.cfg.run.reuse_transcript and self.cfg.run.regenerate != "always":
            for fmt in self.cfg.output.formats:
                path = subtitles.dest_for(local, src, fmt)
                existing = subtitles.load(path)
                if existing:
                    log.info("  translating from %s (%d cues), no new transcription",
                             path.name, len(existing))
                    cache[src] = existing
                    return existing
        return self._transcribed(local, src, cache)

    def _via_llm(self, local, scene, src, lang, cache, duration):
        """(cues or None, salvage needs registering, (action, detail))."""
        cfg = self.cfg
        cues = self._source_cues(local, src, cache)
        # Keep the transcript we just paid for even if the LLM step fails,
        # otherwise minutes of CPU work go in the bin.
        salvage = subtitles.dest_for(local, src)
        salvage_new = False
        if (cues and not cfg.run.dry_run
                and subtitles.should_write(salvage, cfg.run.regenerate)
                and captions.existing_file(local, scene, src) is None):
            self.write(cues, local, src, src, duration)
            salvage_new = any(not captions.registered(scene, src, ext=f)
                              for f in cfg.output.formats)
        if not cfg.ollama.url:
            log.info("  cannot produce %s: %s cannot translate and no OLLAMA_URL "
                     "is set. Set OLLAMA_URL, or TRANSLATE_MODEL=large-v3 for "
                     "English output.", lang, cfg.model.name)
            return None, salvage_new, (
                outcomes.UNSUPPORTED,
                f"{cfg.model.name} cannot translate and OLLAMA_URL is unset")
        self.store.update(stage=f"translating {src} → {lang} (llm)", position=0.0)

        def on_progress(done, total):
            self.store.update(position=duration * done / max(1, total))

        try:
            translated = self.ollama.translate(cues, src, lang,
                                               on_progress=on_progress)
            return translated, salvage_new, ("", "")
        except Exception as e:
            log.info("  translation to %s failed: %s", lang, e)
            log.info("  source transcript kept as %s", salvage.name)
            return None, salvage_new, (outcomes.ERROR, f"translation failed: {e}")

    # -- loop -------------------------------------------------------------

    def run(self):
        cfg = self.cfg
        poll = cfg.run.poll_seconds
        while not self.control.stopping:
            if self.control.paused:
                self.store.update(status="paused", next_poll=None)
                self.control.sleep(1.0)
                continue
            try:
                scenes = self.library.poll()
                self.publish_requests()
            except Exception as e:
                log.error("could not read the library (%s): %s",
                          self.library.label(), e)
                self.store.update(status="error", stage=str(e)[:200],
                                  next_poll=time.time() + poll)
                if cfg.run.run_once:
                    return 1
                self.control.sleep(poll)
                continue

            self.store.update(queue=len(scenes))
            if scenes:
                log.info("queue: %d scene(s)", len(scenes))
            pending_scans = set()
            # How many scenes each directory still has coming. Counted rather
            # than inferred from the path changing between scenes: the sort is
            # an optimisation Stash may refuse, and on an id-sorted queue the
            # same directory comes back dozens of times.
            remaining = Counter(self.library.parent_of(s) for s in scenes)
            last_scan = time.monotonic()
            for i, scene in enumerate(scenes):
                if self.control.stopping or self.control.paused:
                    break
                self.store.update(queue=len(scenes) - i - 1)
                result = outcomes.failed("unhandled error")
                try:
                    result = self.process_scene(scene)
                except Exception as e:
                    log.error("  FAILED: %s: %s", type(e).__name__, e)
                    self.store.add_completed(
                        f"FAILED scene {scene['id']}: {type(e).__name__}")
                if cfg.run.dry_run:
                    continue
                parent = self.library.parent_of(scene)
                remaining[parent] -= 1
                try:
                    self.library.finish(scene, result)
                    if result.needs_scan:
                        pending_scans.add(parent)
                except Exception as e:
                    log.error("  could not record the outcome: %s", e)
                # Nothing more is coming for this directory, so it will not
                # get a better moment than now. The interval covers the one
                # that takes longer to finish than a caption can wait.
                if pending_scans and (
                        not remaining[parent]
                        or time.monotonic() - last_scan >= SCAN_INTERVAL):
                    self.library.flush(pending_scans)
                    last_scan = time.monotonic()

            # Also covers breaking out of the loop for pause or stop, so a
            # written caption is never left unregistered.
            self.library.flush(pending_scans)

            if cfg.run.run_once:
                log.info("done (RUN_ONCE)")
                return 0
            self.store.update(status="idle", scene=None, scene_id=None, stage=None,
                              target=None, targets=[], position=0.0, duration=0.0,
                              next_poll=time.time() + poll)
            self.control.sleep(poll)
        return 0
