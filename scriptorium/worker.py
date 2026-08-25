"""The queue loop: find tagged scenes, produce subtitles, swap the tags."""

import logging
import threading
import time
from collections import Counter
from pathlib import Path

from . import __version__, captions, langs, outcomes, subtitles, tags
from .asr import Models, whisper_translates
from .audio import probe_duration
from .paths import PathMapper
from .stash import Client
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
    def __init__(self, cfg, store, control=None, client=None):
        self.cfg = cfg
        self.store = store
        self.control = control or Control()
        self.client = client or Client(cfg.stash.url, cfg.stash.api_key)
        self.mapper = PathMapper(cfg.stash.path_from, cfg.stash.path_to)
        self.models = Models(cfg.model)
        self.ollama = Ollama(cfg.ollama)
        self.discover, self._discover_note = tags.discovery_enabled(cfg.tags)
        self.plan = tags.Plan()
        # Destinations already produced for the scene in hand; see write().
        self._written = set()

    # -- setup ------------------------------------------------------------

    def bootstrap(self):
        self.client.probe_captions()
        done_id, failed_id = tags.bootstrap(self.client, self.cfg.tags)
        self.done_id, self.failed_id = done_id, failed_id
        if self._discover_note:
            log.info("%s", self._discover_note)
        self.refresh_plan()

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
                    "Non-English audio tagged subs:en will be skipped. "
                    "Set OLLAMA_URL, or TRANSLATE_MODEL=large-v3.", self.cfg.model.name)

        if self.cfg.ollama.url:
            if not self.ollama.ready():
                log.warning("WARNING: LLM translation unavailable — targets that "
                            "need it will be skipped, but transcription still works")
        else:
            log.info("OLLAMA_URL not set — only source-language and English "
                     "output are possible")

    def refresh_plan(self):
        if self.discover:
            self.plan = tags.discover(self.client, self.cfg.tags,
                                      self.done_id, self.failed_id,
                                      previous=self.plan)
        else:
            self.plan = tags.fixed(self.client, self.cfg.tags,
                                   self.done_id, self.failed_id)
        self.store.update(request_tags=self.plan.names())
        return self.plan

    # -- per scene --------------------------------------------------------

    def targets_for(self, scene):
        """The languages this scene is asking for, by tag id.

        Matched on id against the plan used for the query, not by re-parsing
        names, so a tag created mid-scene cannot be mistaken for one we acted on.

        Deduplicated: `subs:en` and `subs:eng` are different tags naming the
        same language, and a scene carrying both should be transcribed once.
        """
        wanted = []
        for t in scene["tags"]:
            req = self.plan.requests.get(t["id"])
            if req is not None and req.lang not in wanted:
                wanted.append(req.lang)
        return wanted

    def _progress(self, position):
        self.store.update(position=position)

    def process_scene(self, scene):
        files = scene.get("files") or []
        if not files:
            log.info("scene %s: no file attached, skipping", scene["id"])
            return outcomes.failed("no file attached")

        local = self.mapper.to_local(files[0]["path"])
        label = scene.get("title") or local.name
        log.info("scene %s: %s", scene["id"], label)

        if not local.exists():
            log.error("  ERROR path not visible to this container: %s", local)
            return outcomes.failed(f"path not visible: {local}")

        wanted = self.targets_for(scene)
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
        self.store.update(source_lang=src, lang_confidence=prob)

        cache = {}
        self._written = set()
        produced = tuple(self.produce(local, scene, src, t, cache, duration)
                         for t in wanted)
        result = outcomes.Scene(targets=produced)
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

        # Stash may already carry this language under another spelling of the
        # same code; writing ours as well would just add a duplicate track.
        if cfg.run.regenerate != "always":
            covered = captions.existing_file(local, scene, lang)
            if covered is not None:
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

    @staticmethod
    def parent_of(scene):
        """The directory Stash keeps the scene's file in.

        Stash's own path, not the mapped local one — this is what Stash is
        told to go and look at.
        """
        files = scene.get("files") or []
        return str(Path(files[0]["path"]).parent) if files else ""

    def flush_scans(self, pending):
        """Ask Stash to rescan the directories that gained a caption.

        One job for the batch rather than one per scene: the old shape had
        Stash rescanning a directory once per file written into it, which on a
        long queue keeps a scan running continuously against the same database
        the worker is still swapping tags in.
        """
        if not pending:
            return
        paths = sorted(pending)
        pending.clear()
        try:
            self.client.metadata_scan(paths)
        except Exception as e:
            log.error("  could not ask Stash to rescan %d path(s): %s",
                      len(paths), e)

    def swap_tags(self, scene, ok):
        """Replace the request tags with done/failed.

        The scene's tags are re-read first: sceneUpdate replaces the whole
        list, and the poll-time snapshot can be an hour old on a long scene,
        so writing it back would clobber anything added meanwhile.
        """
        handled = {t["id"] for t in scene["tags"] if t["id"] in self.plan.requests}
        try:
            current = self.client.scene_tags(scene["id"]) or scene["tags"]
        except Exception:
            current = scene["tags"]
        keep = [t["id"] for t in current if t["id"] not in handled]
        keep.append(self.done_id if ok else self.failed_id)
        self.client.set_scene_tags(scene["id"], sorted(set(keep)))

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
                # Re-read the tag set each poll so a language tag created
                # since startup is honoured without a restart.
                plan = self.refresh_plan()
                scenes = self.client.find_tagged_scenes(plan.ids) if plan.ids else []
            except Exception as e:
                log.error("could not reach Stash: %s", e)
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
            remaining = Counter(self.parent_of(s) for s in scenes)
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
                parent = self.parent_of(scene)
                remaining[parent] -= 1
                try:
                    self.swap_tags(scene, result.ok)
                    if result.needs_scan:
                        pending_scans.add(parent)
                except Exception as e:
                    log.error("  could not update tags: %s", e)
                # Nothing more is coming for this directory, so it will not
                # get a better moment than now. The interval covers the one
                # that takes longer to finish than a caption can wait.
                if pending_scans and (
                        not remaining[parent]
                        or time.monotonic() - last_scan >= SCAN_INTERVAL):
                    self.flush_scans(pending_scans)
                    last_scan = time.monotonic()

            # Also covers breaking out of the loop for pause or stop, so a
            # written caption is never left unregistered.
            self.flush_scans(pending_scans)

            if cfg.run.run_once:
                log.info("done (RUN_ONCE)")
                return 0
            self.store.update(status="idle", scene=None, scene_id=None, stage=None,
                              target=None, targets=[], position=0.0, duration=0.0,
                              next_poll=time.time() + poll)
            self.control.sleep(poll)
        return 0
