"""Ollama translation, used when Whisper cannot reach the target language.

Takes and returns plain cue tuples; timings are never altered.
"""

import json
import logging
import re
import urllib.request

from .langs import name_of

log = logging.getLogger(__name__)

SPECIALIST_HINTS = ("translategemma", "translator", "opus-mt", "madlad")


class TranslateError(RuntimeError):
    pass


def resolve_mode(cfg) -> str:
    """Dedicated translation models will not emit structured JSON."""
    if cfg.mode != "auto":
        return cfg.mode
    name = cfg.model.lower()
    return "lines" if any(k in name for k in SPECIALIST_HINTS) else "json"


class Ollama:
    def __init__(self, cfg):
        self.cfg = cfg

    def _post(self, path, body, timeout=1800):
        req = urllib.request.Request(
            f"{self.cfg.url}{path}", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        return urllib.request.urlopen(req, timeout=timeout)

    def chat(self, prompt, json_format=False):
        body = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0.2},
        }
        if json_format:
            body["format"] = "json"
        with self._post("/api/chat", body) as r:
            return json.loads(r.read().decode())["message"]["content"]

    def ready(self) -> bool:
        """Check Ollama is up and the model present, pulling it if not."""
        if not self.cfg.url:
            return False
        try:
            with urllib.request.urlopen(f"{self.cfg.url}/api/tags", timeout=30) as r:
                have = {m["name"] for m in json.loads(r.read().decode()).get("models", [])}
        except Exception as e:
            log.info("Ollama unreachable at %s: %s", self.cfg.url, e)
            return False

        if self.cfg.model in have or f"{self.cfg.model}:latest" in have:
            log.info("Ollama ready, model %s present", self.cfg.model)
            return True
        if not self.cfg.pull:
            log.info("model %s not present and OLLAMA_PULL=0", self.cfg.model)
            return False

        log.info("pulling %s — this runs once and may take a while", self.cfg.model)
        try:
            with self._post("/api/pull", {"model": self.cfg.model, "stream": True}) as r:
                last = 0
                for raw in r:
                    if not raw.strip():
                        continue
                    msg = json.loads(raw.decode())
                    if msg.get("error"):
                        log.info("pull failed: %s", msg["error"])
                        return False
                    total, done = msg.get("total"), msg.get("completed")
                    if total and done:
                        pct = done / total * 100
                        if pct - last >= 10:
                            last = pct
                            log.info("  %s %.0f%%", msg.get("status", ""), pct)
        except Exception as e:
            log.info("pull failed: %s", e)
            return False
        log.info("model %s ready", self.cfg.model)
        return True

    def translate(self, cues, src, dst, on_progress=None):
        if not self.cfg.url:
            raise TranslateError("OLLAMA_URL not set — cannot translate to " + dst)
        src_name, dst_name = name_of(src), name_of(dst)
        mode = resolve_mode(self.cfg)
        batch_size = self.cfg.batch
        out = []

        for i in range(0, len(cues), batch_size):
            batch = cues[i:i + batch_size]
            texts = [c[2].replace("\n", " ").strip() for c in batch]
            if mode == "json":
                lines = self._batch_json(texts, src_name, dst_name, i)
            else:
                lines = self._batch_lines(texts, src, dst, src_name, dst_name, i)
            for (start, end, _), text in zip(batch, lines):
                out.append((start, end, text or ""))
            done = min(i + batch_size, len(cues))
            if on_progress is not None:
                on_progress(done, len(cues))
            log.info("  translated %d/%d lines", done, len(cues))
        return out

    def _batch_json(self, texts, src_name, dst_name, offset):
        prompt = (
            f"Translate these {src_name} subtitle lines into {dst_name}.\n"
            f"They are consecutive lines of one conversation — use that context.\n"
            f"Keep each translation about the same length so it fits on screen.\n"
            f"Return ONLY a JSON object with the same keys and translated values.\n\n"
            + json.dumps({str(n): t for n, t in enumerate(texts)}, ensure_ascii=False)
        )
        try:
            got = json.loads(self.chat(prompt, json_format=True))
            return [got.get(str(n), texts[n]) for n in range(len(texts))]
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            log.info("  batch at %d: bad JSON, keeping source text", offset)
            return list(texts)

    def _batch_lines(self, texts, src, dst, src_name, dst_name, offset):
        prompt = (
            f"You are a professional {src_name} ({src}) to {dst_name} ({dst}) "
            f"translator. Translate each numbered line below. Produce only the "
            f"{dst_name} translation, one per line, numbered identically, "
            f"with no commentary.\n\n"
            + "\n".join(f"{n + 1}. {t}" for n, t in enumerate(texts))
        )
        reply = self.chat(prompt)
        lines = []
        for raw in reply.strip().splitlines():
            raw = raw.strip()
            if raw:
                lines.append(re.sub(r"^\s*\d+[.)]\s*", "", raw))
        if len(lines) == len(texts):
            return lines

        # A count mismatch means alignment is lost, which would shift every
        # later subtitle. Redo the batch one line at a time instead.
        log.info("  batch at %d: got %d of %d lines, falling back to per-line",
                 offset, len(lines), len(texts))
        lines = []
        for t in texts:
            single = (
                f"You are a professional {src_name} ({src}) to {dst_name} "
                f"({dst}) translator. Produce only the {dst_name} "
                f"translation, without commentary.\n\n{t}"
            )
            try:
                lines.append(self.chat(single).strip().splitlines()[0])
            except Exception:
                lines.append(t)
        return lines
