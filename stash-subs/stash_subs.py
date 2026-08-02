#!/usr/bin/env python3
"""
stash-subs — tag-driven subtitle generation for a Stash library.

Workflow:
  1. In Stash, tag a scene with `subs:en` and/or `subs:es` (or `subs:auto`).
  2. This worker polls Stash's GraphQL API for scenes carrying those tags.
  3. It transcribes / translates, writes `<basename>.<lang>.srt` beside the video.
  4. It swaps the request tag for `subs:done` (or `subs:failed`) and triggers a
     targeted rescan so Stash picks the caption up.

Nothing is scanned that you haven't tagged.
"""

import html
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --------------------------------------------------------------------------
# Config (all via environment variables)
# --------------------------------------------------------------------------

STASH_URL = os.getenv("STASH_URL", "http://stash:9999").rstrip("/")
STASH_API_KEY = os.getenv("STASH_API_KEY", "").strip()

# Map Stash's view of a path -> this container's view. Both mount the same
# host dir at /data, so the default is a no-op.
PATH_FROM = os.getenv("PATH_FROM", "/data")
PATH_TO = os.getenv("PATH_TO", "/data")

MODEL_NAME = os.getenv("MODEL", "large-v3-turbo")
MODEL_DIR = os.getenv("MODEL_DIR", "/models")
DEVICE = os.getenv("DEVICE", "cpu")
COMPUTE_TYPE = os.getenv("COMPUTE_TYPE", "int8")
THREADS = int(os.getenv("THREADS", "6"))
BEAM_SIZE = int(os.getenv("BEAM_SIZE", "5"))
# Turbo models cannot translate. If you set this to a full model (e.g.
# `large-v3`) it is used for speech->English instead of the LLM path.
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL", "").strip()

# Tags that request work. `subs:auto` = transcribe in whatever language is spoken.
REQUEST_TAGS = [t.strip() for t in
                os.getenv("REQUEST_TAGS", "subs:en,subs:es,subs:auto").split(",") if t.strip()]
DONE_TAG = os.getenv("DONE_TAG", "subs:done")
FAILED_TAG = os.getenv("FAILED_TAG", "subs:failed")

HTTP_PORT = int(os.getenv("HTTP_PORT", "8088"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
RUN_ONCE = os.getenv("RUN_ONCE", "0") == "1"
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
OVERWRITE = os.getenv("OVERWRITE", "0") == "1"

# Optional LLM translation (used only when the target language is neither the
# source language nor English — e.g. English audio -> Spanish subtitles).
OLLAMA_URL = os.getenv("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "translategemma:4b")
OLLAMA_BATCH = int(os.getenv("OLLAMA_BATCH", "20"))
OLLAMA_PULL = os.getenv("OLLAMA_PULL", "1") == "1"
# "auto" picks `lines` for dedicated translation models (which won't emit JSON)
# and `json` for general chat models (which handle it and keep context better).
TRANSLATE_MODE = os.getenv("TRANSLATE_MODE", "auto")

LANG_NAMES = {
    "en": "English", "es": "Spanish", "de": "German", "fr": "French",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ja": "Japanese",
    "pl": "Polish", "ru": "Russian", "cs": "Czech", "sv": "Swedish",
}

# Segments matching these are almost always Whisper hallucinating on non-speech.
JUNK_PATTERNS = [
    re.compile(p, re.I) for p in [
        r"^\s*subtitle[sd]?\s+by\b", r"^\s*subs?\s+by\b", r"\bamara\.org\b",
        r"^\s*thanks?\s+for\s+watching", r"^\s*thank\s+you\s+for\s+watching",
        r"^\s*please\s+subscribe", r"^\s*www\.", r"^\s*http", r"^\s*\[?music\]?\s*$",
        r"^\s*subtítulos\s+(por|realizados)", r"^\s*¡?\s*gracias\s+por\s+ver",
    ]
]


# --------------------------------------------------------------------------
# Shared state for the status page
# --------------------------------------------------------------------------

LOCK = threading.Lock()
LOG_LINES = deque(maxlen=200)
STATE = {
    "started": time.time(),
    "status": "starting",      # starting | idle | working | error
    "queue": 0,
    "scene": None,             # title of the scene being worked on
    "scene_id": None,
    "stage": None,             # what we're doing to it right now
    "source_lang": None,
    "lang_confidence": None,
    "duration": 0.0,           # seconds of media
    "position": 0.0,           # seconds decoded so far
    "target": None,            # language currently being produced
    "targets": [],
    "started_scene": None,
    "completed": deque(maxlen=25),
    "next_poll": None,
}


def set_state(**kw):
    with LOCK:
        STATE.update(kw)


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOCK:
        LOG_LINES.append(line)


# --------------------------------------------------------------------------
# Stash GraphQL
# --------------------------------------------------------------------------

def gql(query, variables=None):
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    headers = {"Content-Type": "application/json"}
    if STASH_API_KEY:
        headers["ApiKey"] = STASH_API_KEY
    req = urllib.request.Request(f"{STASH_URL}/graphql", data=payload, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Stash HTTP {e.code}: {e.read().decode()[:400]}") from None
    if body.get("errors"):
        raise RuntimeError(f"Stash GraphQL error: {body['errors']}")
    return body["data"]


def ensure_tag(name):
    """Return the tag id for `name`, creating the tag if it does not exist."""
    data = gql(
        """query($n: String!) {
             findTags(tag_filter: {name: {value: $n, modifier: EQUALS}},
                      filter: {per_page: -1}) { tags { id name } }
           }""",
        {"n": name},
    )
    for t in data["findTags"]["tags"]:
        if t["name"].lower() == name.lower():
            return t["id"]
    data = gql(
        "mutation($i: TagCreateInput!) { tagCreate(input: $i) { id name } }",
        {"i": {"name": name}},
    )
    log(f"created tag {name}")
    return data["tagCreate"]["id"]


def find_tagged_scenes(tag_ids):
    data = gql(
        """query($ids: [ID!]) {
             findScenes(
               scene_filter: {tags: {value: $ids, modifier: INCLUDES, depth: 0}},
               filter: {per_page: -1, sort: "id", direction: ASC}
             ) {
               count
               scenes { id title files { path duration } tags { id name } }
             }
           }""",
        {"ids": tag_ids},
    )
    return data["findScenes"]["scenes"]


def set_scene_tags(scene_id, tag_ids):
    gql(
        "mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
        {"i": {"id": scene_id, "tag_ids": tag_ids}},
    )


def rescan_path(path):
    """Targeted rescan so Stash associates the new caption file."""
    gql(
        "mutation($i: ScanMetadataInput!) { metadataScan(input: $i) }",
        {"i": {"paths": [path]}},
    )


# --------------------------------------------------------------------------
# Audio helpers
# --------------------------------------------------------------------------

def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def sample_audio(path, duration, out_wav):
    """
    Concatenate three 45s chunks from 25%/50%/75% into one wav.

    Whisper's built-in language detection only looks at the first 30 seconds,
    which in a lot of libraries is music, an intro card, or silence. Sampling
    from the middle of the file is far more reliable.
    """
    points = [duration * f for f in (0.25, 0.5, 0.75)] if duration > 300 else [0.0]
    parts = []
    for i, start in enumerate(points):
        p = f"{out_wav}.{i}.wav"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", str(int(start)), "-t", "45",
             "-i", str(path), "-ac", "1", "-ar", "16000", "-vn", p],
            check=True, timeout=300,
        )
        parts.append(p)
    if len(parts) == 1:
        os.replace(parts[0], out_wav)
        return
    lst = f"{out_wav}.txt"
    with open(lst, "w") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", lst, "-c", "copy", out_wav], check=True, timeout=300)
    for p in parts + [lst]:
        try:
            os.remove(p)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------

_models = {}


def get_model(name=None):
    name = name or MODEL_NAME
    if name not in _models:
        from faster_whisper import WhisperModel
        log(f"loading model {name} ({COMPUTE_TYPE}, {THREADS} threads)")
        _models[name] = WhisperModel(
            name, device=DEVICE, compute_type=COMPUTE_TYPE,
            cpu_threads=THREADS, download_root=MODEL_DIR,
        )
    return _models[name]


def whisper_translates(name=None):
    """
    Whisper's `turbo` checkpoints were fine-tuned on transcription data only,
    with translation excluded. Asking them for task="translate" does not fail —
    it silently returns a transcript in the source language. So we must not
    route English output through Whisper when running a turbo model.
    """
    return "turbo" not in (name or MODEL_NAME).lower()


def detect_language(path, duration):
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "sample.wav")
        try:
            sample_audio(path, duration, wav)
        except Exception as e:
            log(f"  language sampling failed ({e}); falling back to file start")
            wav = str(path)
        _, info = get_model().transcribe(wav, language=None, vad_filter=True, beam_size=1)
        return info.language, info.language_probability


def clean(segments, report_progress=False):
    """Drop hallucinations: junk phrases, silence artefacts, repeat loops.

    faster-whisper yields segments lazily, so consuming them here doubles as a
    progress signal: each segment's end timestamp is how far into the media we
    have decoded.
    """
    out, prev, repeats = [], None, 0
    for s in segments:
        if report_progress:
            set_state(position=s.end)
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


def transcribe(path, language, task="transcribe", model=None):
    segments, info = get_model(model).transcribe(
        str(path),
        language=language,
        task=task,
        beam_size=BEAM_SIZE,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 700, "speech_pad_ms": 300},
        condition_on_previous_text=False,   # stops repetition death-spirals
        no_speech_threshold=0.6,
        temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    )
    return clean(segments, report_progress=True), info


# --------------------------------------------------------------------------
# SRT
# --------------------------------------------------------------------------

def ts(seconds):
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(cues, dest):
    """Write atomically — a half-written SRT scanned by Stash is a bad time."""
    tmp = Path(str(dest) + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(cues, 1):
            f.write(f"{i}\n{ts(start)} --> {ts(end)}\n{text}\n\n")
    os.replace(tmp, dest)


# --------------------------------------------------------------------------
# LLM translation (English audio -> Spanish subs, etc.)
# --------------------------------------------------------------------------

def ollama_post(path, body, timeout=1800):
    req = urllib.request.Request(
        f"{OLLAMA_URL}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


def ollama_ready():
    """Verify Ollama is up and make sure the model is present, pulling if not."""
    if not OLLAMA_URL:
        return False
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=30) as r:
            have = {m["name"] for m in json.loads(r.read().decode()).get("models", [])}
    except Exception as e:
        log(f"Ollama unreachable at {OLLAMA_URL}: {e}")
        return False

    if OLLAMA_MODEL in have or f"{OLLAMA_MODEL}:latest" in have:
        log(f"Ollama ready, model {OLLAMA_MODEL} present")
        return True
    if not OLLAMA_PULL:
        log(f"model {OLLAMA_MODEL} not present and OLLAMA_PULL=0")
        return False

    log(f"pulling {OLLAMA_MODEL} — this runs once and may take a while")
    set_state(status="working", scene=f"pulling {OLLAMA_MODEL}", stage="downloading model")
    try:
        with ollama_post("/api/pull", {"model": OLLAMA_MODEL, "stream": True}) as r:
            last = 0
            for raw in r:
                if not raw.strip():
                    continue
                msg = json.loads(raw.decode())
                if msg.get("error"):
                    log(f"pull failed: {msg['error']}")
                    return False
                total, done = msg.get("total"), msg.get("completed")
                if total and done:
                    pct = done / total * 100
                    if pct - last >= 10:
                        last = pct
                        log(f"  {msg.get('status','')} {pct:.0f}%")
    except Exception as e:
        log(f"pull failed: {e}")
        return False
    log(f"model {OLLAMA_MODEL} ready")
    return True


def _translate_mode():
    if TRANSLATE_MODE != "auto":
        return TRANSLATE_MODE
    name = OLLAMA_MODEL.lower()
    specialist = any(k in name for k in ("translategemma", "translator", "opus-mt", "madlad"))
    return "lines" if specialist else "json"


def _chat(prompt, json_format):
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    if json_format:
        body["format"] = "json"
    with ollama_post("/api/chat", body) as r:
        return json.loads(r.read().decode())["message"]["content"]


def ollama_translate(cues, src, dst):
    if not OLLAMA_URL:
        raise RuntimeError("OLLAMA_URL not set — cannot translate to " + dst)
    src_name = LANG_NAMES.get(src, src)
    dst_name = LANG_NAMES.get(dst, dst)
    mode = _translate_mode()
    out = []

    for i in range(0, len(cues), OLLAMA_BATCH):
        batch = cues[i:i + OLLAMA_BATCH]
        texts = [c[2].replace("\n", " ").strip() for c in batch]

        if mode == "json":
            prompt = (
                f"Translate these {src_name} subtitle lines into {dst_name}.\n"
                f"They are consecutive lines of one conversation — use that context.\n"
                f"Keep each translation about the same length so it fits on screen.\n"
                f"Return ONLY a JSON object with the same keys and translated values.\n\n"
                + json.dumps({str(n): t for n, t in enumerate(texts)}, ensure_ascii=False)
            )
            try:
                got = json.loads(_chat(prompt, json_format=True))
                lines = [got.get(str(n), texts[n]) for n in range(len(texts))]
            except (json.JSONDecodeError, KeyError, TypeError):
                log(f"  batch at {i}: bad JSON, keeping source text")
                lines = texts
        else:
            # Dedicated translation models: one line in, one line out.
            prompt = (
                f"You are a professional {src_name} ({src}) to {dst_name} ({dst}) "
                f"translator. Translate each numbered line below. Produce only the "
                f"{dst_name} translation, one per line, numbered identically, "
                f"with no commentary.\n\n"
                + "\n".join(f"{n+1}. {t}" for n, t in enumerate(texts))
            )
            reply = _chat(prompt, json_format=False)
            lines = []
            for raw in reply.strip().splitlines():
                raw = raw.strip()
                if raw:
                    lines.append(re.sub(r"^\s*\d+[\.\)]\s*", "", raw))
            if len(lines) != len(texts):
                # Count mismatch means alignment is lost; redo line by line.
                log(f"  batch at {i}: got {len(lines)} of {len(texts)} lines, "
                    f"falling back to per-line")
                lines = []
                for t in texts:
                    single = (
                        f"You are a professional {src_name} ({src}) to {dst_name} "
                        f"({dst}) translator. Produce only the {dst_name} "
                        f"translation, without commentary.\n\n{t}"
                    )
                    try:
                        lines.append(_chat(single, json_format=False).strip().splitlines()[0])
                    except Exception:
                        lines.append(t)

        for (start, end, _), text in zip(batch, lines):
            out.append((start, end, text or ""))
        done = min(i + OLLAMA_BATCH, len(cues))
        set_state(position=STATE["duration"] * done / max(1, len(cues)))
        log(f"  translated {done}/{len(cues)} lines")

    return out


# --------------------------------------------------------------------------
# Status page
# --------------------------------------------------------------------------

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>stash-subs</title>
<meta http-equiv="refresh" content="5">
<style>
  body {{ background:#16181d; color:#d6d9de; margin:0;
         font:14px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  .wrap {{ max-width:900px; margin:0 auto; padding:28px 20px 60px; }}
  h1 {{ font-size:15px; letter-spacing:.14em; text-transform:uppercase;
        color:#7d838d; font-weight:600; margin:0 0 22px; }}
  .card {{ background:#1d2027; border:1px solid #2b2f38; border-radius:8px;
           padding:18px 20px; margin-bottom:16px; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:11px;
            font-size:11px; letter-spacing:.09em; text-transform:uppercase; }}
  .working {{ background:#1e3a5f; color:#7cb8ff; }}
  .idle    {{ background:#2b2f38; color:#8a919c; }}
  .error   {{ background:#4a2020; color:#ff9c9c; }}
  .starting{{ background:#3d3517; color:#e0c060; }}
  .title {{ font-size:16px; color:#f0f2f5; margin:12px 0 4px; word-break:break-all; }}
  .meta {{ color:#7d838d; font-size:12.5px; }}
  .bar {{ height:7px; background:#2b2f38; border-radius:4px;
          overflow:hidden; margin:16px 0 7px; }}
  .fill {{ height:100%; background:linear-gradient(90deg,#3b7dd8,#5aa0f0);
           transition:width .4s; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  td {{ padding:4px 0; border-bottom:1px solid #24272f; }}
  td:first-child {{ color:#7d838d; width:150px; }}
  pre {{ background:#12141a; border:1px solid #2b2f38; border-radius:6px;
         padding:14px; overflow-x:auto; font-size:12.5px; color:#aab0ba;
         max-height:400px; margin:0; }}
  h2 {{ font-size:12px; letter-spacing:.12em; text-transform:uppercase;
        color:#7d838d; margin:0 0 10px; }}
  a {{ color:#5aa0f0; }}
</style></head><body><div class="wrap">
<h1>stash-subs</h1>
{body}
<div class="card"><h2>Log</h2><pre>{logs}</pre></div>
<p class="meta">Auto-refreshes every 5s &middot; uptime {uptime}</p>
</div></body></html>"""


def fmt_hms(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render():
    with LOCK:
        st = dict(STATE)
        logs = "\n".join(LOG_LINES)
        completed = list(st["completed"])

    status = st["status"]
    parts = [f'<div class="card"><span class="badge {status}">{status}</span>']

    if status == "working" and st["scene"]:
        pct = (st["position"] / st["duration"] * 100) if st["duration"] else 0
        pct = max(0, min(100, pct))
        elapsed = time.time() - (st["started_scene"] or time.time())
        eta = (elapsed / pct * (100 - pct)) if pct > 2 else 0
        speed = (st["position"] / elapsed) if elapsed > 0 else 0
        parts.append(f'<div class="title">{html.escape(st["scene"])}</div>')
        parts.append(
            f'<div class="meta">scene {st["scene_id"]} &middot; '
            f'{html.escape(st["stage"] or "")}</div>'
        )
        parts.append(f'<div class="bar"><div class="fill" style="width:{pct:.1f}%"></div></div>')
        parts.append(
            f'<div class="meta">{pct:.0f}% &middot; '
            f'{fmt_hms(st["position"])} / {fmt_hms(st["duration"])} &middot; '
            f'{speed:.1f}x realtime'
            + (f' &middot; ~{fmt_hms(eta)} left' if eta else '')
            + '</div>'
        )
        parts.append('<table>')
        if st["source_lang"]:
            conf = f' ({st["lang_confidence"]:.0%})' if st["lang_confidence"] else ''
            parts.append(f'<tr><td>source language</td><td>{st["source_lang"]}{conf}</td></tr>')
        if st["targets"]:
            parts.append(f'<tr><td>producing</td><td>{", ".join(st["targets"])}</td></tr>')
        parts.append(f'<tr><td>queue</td><td>{st["queue"]} waiting</td></tr>')
        parts.append('</table>')
    else:
        nxt = st.get("next_poll")
        wait = f' &middot; next poll in {max(0, int(nxt - time.time()))}s' if nxt else ''
        parts.append(
            f'<div class="title">{st["queue"]} scene(s) queued</div>'
            f'<div class="meta">Tag scenes in Stash with '
            f'{", ".join(REQUEST_TAGS)} to add work{wait}</div>'
        )
    parts.append('</div>')

    if completed:
        rows = "".join(
            f'<tr><td>{time.strftime("%H:%M", time.localtime(c["at"]))}</td>'
            f'<td>{html.escape(c["what"])}</td></tr>'
            for c in reversed(completed)
        )
        parts.append(f'<div class="card"><h2>Recently finished</h2><table>{rows}</table></div>')

    return PAGE.format(
        body="".join(parts),
        logs=html.escape(logs) or "(nothing yet)",
        uptime=fmt_hms(time.time() - st["started"]),
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") in ("", "/status"):
            payload, ctype = render().encode(), "text/html; charset=utf-8"
        elif self.path.rstrip("/") == "/json":
            with LOCK:
                st = {k: (list(v) if isinstance(v, deque) else v)
                      for k, v in STATE.items()}
            payload, ctype = json.dumps(st, default=str).encode(), "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass          # don't spam our own log with request lines


def start_http():
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log(f"status page on http://0.0.0.0:{HTTP_PORT}")


# --------------------------------------------------------------------------
# Per-scene processing
# --------------------------------------------------------------------------

def targets_for(scene):
    wanted = []
    for t in scene["tags"]:
        name = t["name"].lower()
        if name in (n.lower() for n in REQUEST_TAGS):
            wanted.append(name.split(":", 1)[1])
    return wanted


def process_scene(scene):
    files = scene.get("files") or []
    if not files:
        log(f"scene {scene['id']}: no file attached, skipping")
        return False

    stash_path = files[0]["path"]
    local = Path(stash_path.replace(PATH_FROM, PATH_TO, 1))
    label = scene.get("title") or local.name
    log(f"scene {scene['id']}: {label}")

    if not local.exists():
        log(f"  ERROR path not visible to this container: {local}")
        return False

    wanted = targets_for(scene)
    if not wanted:
        return False

    duration = files[0].get("duration") or probe_duration(local)
    set_state(status="working", scene=label, scene_id=scene["id"],
              stage="detecting language", targets=wanted, duration=duration,
              position=0.0, started_scene=time.time(),
              source_lang=None, lang_confidence=None)

    src, prob = detect_language(local, duration)
    log(f"  source language: {src} ({prob:.0%} confident), {duration/60:.0f} min")
    set_state(source_lang=src, lang_confidence=prob)

    cache = {}   # language -> cues, so we transcribe at most once

    for target in wanted:
        lang = src if target == "auto" else target
        dest = local.with_suffix("")
        dest = dest.parent / f"{dest.name}.{lang}.srt"

        if dest.exists() and not OVERWRITE:
            log(f"  {dest.name} exists, skipping")
            continue

        set_state(target=lang, position=0.0)

        # Decide how to reach `lang`. Whisper can only translate INTO English,
        # and turbo checkpoints cannot translate at all.
        if lang == src:
            route = "transcribe"
        elif lang == "en" and whisper_translates():
            route = "whisper-translate"
        elif lang == "en" and TRANSLATE_MODEL:
            route = "whisper-translate-alt"
        else:
            route = "llm"

        if route == "transcribe":
            if src not in cache:
                set_state(stage=f"transcribing {src}")
                cache[src], _ = transcribe(local, src, "transcribe")
            cues = cache[src]

        elif route == "whisper-translate":
            set_state(stage=f"translating {src} \u2192 en (whisper)")
            cues, _ = transcribe(local, src, "translate")

        elif route == "whisper-translate-alt":
            set_state(stage=f"translating {src} \u2192 en ({TRANSLATE_MODEL})")
            cues, _ = transcribe(local, src, "translate", model=TRANSLATE_MODEL)

        else:
            if src not in cache:
                set_state(stage=f"transcribing {src}")
                cache[src], _ = transcribe(local, src, "transcribe")
            # Always keep the transcript we just paid for, even if the LLM step
            # then fails — otherwise minutes of CPU work go in the bin.
            salvage = local.parent / f"{local.with_suffix('').name}.{src}.srt"
            if cache[src] and not salvage.exists() and not DRY_RUN:
                write_srt(cache[src], salvage)
                log(f"  wrote {salvage.name} ({len(cache[src])} cues, source language)")
            if not OLLAMA_URL:
                log(f"  cannot produce {lang}: {MODEL_NAME} cannot translate and "
                    f"no OLLAMA_URL is set. Set OLLAMA_URL, or "
                    f"TRANSLATE_MODEL=large-v3 for English output.")
                continue
            set_state(stage=f"translating {src} \u2192 {lang} (llm)", position=0.0)
            try:
                cues = ollama_translate(cache[src], src, lang)
            except Exception as e:
                log(f"  translation to {lang} failed: {e}")
                log(f"  source transcript kept as {salvage.name}")
                continue

        if not cues:
            log(f"  no speech found for {lang}, nothing written")
            continue
        if DRY_RUN:
            log(f"  [dry run] would write {dest.name} ({len(cues)} cues)")
            continue
        write_srt(cues, dest)
        log(f"  wrote {dest.name} ({len(cues)} cues)")
        with LOCK:
            STATE["completed"].append(
                {"at": time.time(), "what": f"{dest.name} — {len(cues)} cues"}
            )

    return True


def main():
    start_http()
    log(f"stash-subs starting — Stash at {STASH_URL}")
    request_tag_ids = {name: ensure_tag(name) for name in REQUEST_TAGS}
    done_id = ensure_tag(DONE_TAG)
    failed_id = ensure_tag(FAILED_TAG)
    req_ids = set(request_tag_ids.values())

    if not whisper_translates():
        how = (f"a second Whisper model ({TRANSLATE_MODEL})" if TRANSLATE_MODEL
               else f"the LLM ({OLLAMA_MODEL})" if OLLAMA_URL else None)
        if how:
            log(f"note: {MODEL_NAME} cannot translate; English output will use {how}")
        else:
            log(f"WARNING: {MODEL_NAME} cannot translate and no fallback is "
                f"configured. Non-English audio tagged subs:en will be skipped. "
                f"Set OLLAMA_URL, or TRANSLATE_MODEL=large-v3.")

    if OLLAMA_URL:
        if not ollama_ready():
            log("WARNING: LLM translation unavailable — targets that need it "
                "will be skipped, but transcription still works")
    else:
        log("OLLAMA_URL not set — only source-language and English output "
            "are possible")
    set_state(status="idle", scene=None, stage=None)

    while True:
        try:
            scenes = find_tagged_scenes(list(req_ids))
        except Exception as e:
            log(f"could not reach Stash: {e}")
            set_state(status="error", stage=str(e)[:200])
            if RUN_ONCE:
                sys.exit(1)
            time.sleep(POLL_SECONDS)
            continue

        set_state(queue=len(scenes))
        if scenes:
            log(f"queue: {len(scenes)} scene(s)")
        for i, scene in enumerate(scenes):
            set_state(queue=len(scenes) - i - 1)
            ok = False
            try:
                ok = process_scene(scene)
            except Exception as e:
                log(f"  FAILED: {type(e).__name__}: {e}")
                with LOCK:
                    STATE["completed"].append(
                        {"at": time.time(),
                         "what": f"FAILED scene {scene['id']}: {type(e).__name__}"}
                    )
            if DRY_RUN:
                continue
            # Swap the request tag(s) out so the scene leaves the queue.
            keep = [t["id"] for t in scene["tags"] if t["id"] not in req_ids]
            keep.append(done_id if ok else failed_id)
            try:
                set_scene_tags(scene["id"], sorted(set(keep)))
                if ok:
                    rescan_path(str(Path(scene["files"][0]["path"]).parent))
            except Exception as e:
                log(f"  could not update tags: {e}")

        if RUN_ONCE:
            log("done (RUN_ONCE)")
            return
        set_state(status="idle", scene=None, scene_id=None, stage=None,
                  target=None, targets=[], position=0.0, duration=0.0,
                  next_poll=time.time() + POLL_SECONDS)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()

