"""The queue loop: find tagged scenes, produce subtitles, swap the tags."""

import logging
import threading
import time
from pathlib import Path

from . import langs, subtitles, tags
from . import __version__
from .asr import Models, whisper_translates
from .audio import probe_duration
from .paths import PathMapper
from .stash import Client
from .translate import Ollama, TranslateError

log = logging.getLogger(__name__)


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

    # -- setup ------------------------------------------------------------

    def bootstrap(self):
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
            return False

        local = self.mapper.to_local(files[0]["path"])
        label = scene.get("title") or local.name
        log.info("scene %s: %s", scene["id"], label)

        if not local.exists():
            log.error("  ERROR path not visible to this container: %s", local)
            return False

        wanted = self.targets_for(scene)
        if not wanted:
            return False

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
        produced = False
        for target in wanted:
            if self.produce(local, scene, src, target, cache, duration):
                produced = True
        return produced or True

    def produce(self, local, scene, src, target, cache, duration):
        cfg = self.cfg
        lang = src if target == tags.AUTO else target

        # Belt and braces: a tag was validated before it got here, but the
        # `auto` target takes its language from Whisper's detector at runtime.
        if not langs.is_caption_suffix(lang):
            log.warning("  refusing to write .%s.srt: %s", lang,
                        langs.reject_reason(lang))
            return False

        dest = subtitles.dest_for(local, lang)

        if not subtitles.should_write(dest, cfg.run.regenerate):
            why = ("not ours to overwrite" if cfg.run.regenerate == "if-ours"
                   else "exists")
            log.info("  %s %s, skipping", dest.name, why)
            return False

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
            cues = self._via_llm(local, src, lang, cache, duration)
            if cues is None:
                return False

        if not cues:
            log.info("  no speech found for %s, nothing written", lang)
            return False
        if cfg.run.dry_run:
            log.info("  [dry run] would write %s (%d cues)", dest.name, len(cues))
            return False
        self.write(cues, dest, src, lang, duration,
                   mt_model=cfg.ollama.model if route == "llm" else "")
        return True

    def write(self, cues, dest, src, lang, duration, mt_model=""):
        """Add the generation marker and write.

        The marker is applied here rather than upstream so it can never be
        handed to the translator: translate() only ever sees transcribed cues.
        """
        cfg = self.cfg
        annotated = subtitles.with_annotation(
            cues, self.provenance(src, lang, mt_model),
            mode=cfg.annotate.mode,
            seconds=cfg.annotate.seconds,
            gap=cfg.annotate.gap,
            media_duration=duration,
            template=cfg.annotate.text or subtitles.DEFAULT_TEMPLATE,
        )
        subtitles.write_srt(annotated, dest)
        log.info("  wrote %s (%d cues)", dest.name, len(cues))
        self.store.add_completed(f"{dest.name} — {len(cues)} cues")

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

    def _via_llm(self, local, src, lang, cache, duration):
        cfg = self.cfg
        cues = self._transcribed(local, src, cache)
        # Keep the transcript we just paid for even if the LLM step fails,
        # otherwise minutes of CPU work go in the bin.
        salvage = subtitles.dest_for(local, src)
        if (cues and not cfg.run.dry_run
                and subtitles.should_write(salvage, cfg.run.regenerate)):
            self.write(cues, salvage, src, src, duration)
        if not cfg.ollama.url:
            log.info("  cannot produce %s: %s cannot translate and no OLLAMA_URL "
                     "is set. Set OLLAMA_URL, or TRANSLATE_MODEL=large-v3 for "
                     "English output.", lang, cfg.model.name)
            return None
        self.store.update(stage=f"translating {src} → {lang} (llm)", position=0.0)

        def on_progress(done, total):
            self.store.update(position=duration * done / max(1, total))

        try:
            return self.ollama.translate(cues, src, lang, on_progress=on_progress)
        except (TranslateError, Exception) as e:
            log.info("  translation to %s failed: %s", lang, e)
            log.info("  source transcript kept as %s", salvage.name)
            return None

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
            for i, scene in enumerate(scenes):
                if self.control.stopping or self.control.paused:
                    break
                self.store.update(queue=len(scenes) - i - 1)
                ok = False
                try:
                    ok = self.process_scene(scene)
                except Exception as e:
                    log.error("  FAILED: %s: %s", type(e).__name__, e)
                    self.store.add_completed(
                        f"FAILED scene {scene['id']}: {type(e).__name__}")
                if cfg.run.dry_run:
                    continue
                try:
                    self.swap_tags(scene, ok)
                    if ok:
                        parent = str(Path(scene["files"][0]["path"]).parent)
                        # The Stash-side path, not the mapped local one.
                        self.client.metadata_scan(parent)
                except Exception as e:
                    log.error("  could not update tags: %s", e)

            if cfg.run.run_once:
                log.info("done (RUN_ONCE)")
                return 0
            self.store.update(status="idle", scene=None, scene_id=None, stage=None,
                              target=None, targets=[], position=0.0, duration=0.0,
                              next_poll=time.time() + poll)
            self.control.sleep(poll)
        return 0
