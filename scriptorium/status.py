"""Shared worker state and the status page that renders it."""

import html
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__


class Store:
    """Worker state, readable from the HTTP thread.

    Owns its own lock. Never log while holding it, and never read it from a
    logging handler — either would deadlock the worker against a request.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._completed = deque(maxlen=25)
        self._state = {
            "started": time.time(),
            "status": "starting",   # starting | idle | working | paused | error
            "queue": 0,
            "scene": None,
            "scene_id": None,
            "stage": None,
            "source_lang": None,
            "lang_confidence": None,
            "duration": 0.0,
            "position": 0.0,
            "target": None,
            "targets": [],
            "started_scene": None,
            "next_poll": None,
            "request_tags": [],
            "poll_note": None,
            "version": __version__,
        }

    def update(self, **kw):
        with self._lock:
            self._state.update(kw)

    def get(self, key, default=None):
        with self._lock:
            return self._state.get(key, default)

    def add_completed(self, what):
        with self._lock:
            self._completed.append({"at": time.time(), "what": what})

    def snapshot(self):
        with self._lock:
            snap = dict(self._state)
            snap["completed"] = list(self._completed)
            return snap


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Scriptorium</title>
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
  .paused  {{ background:#3a3320; color:#d7b471; }}
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
  form {{ display:inline; }}
  button {{ background:#2b2f38; color:#d6d9de; border:1px solid #3a3f4a;
            border-radius:6px; padding:6px 14px; font:inherit; font-size:12.5px;
            cursor:pointer; margin-right:8px; }}
  button:hover {{ background:#343945; }}
  .note {{ color:#7cb8ff; font-size:12.5px; margin-top:10px; }}
</style></head><body><div class="wrap">
<h1>Scriptorium</h1>
{body}
<div class="card"><h2>Log</h2><pre>{logs}</pre></div>
<p class="meta"><a href="https://github.com/Anastylosis/Scriptorium">Scriptorium</a>
{version} &middot; auto-refreshes every 5s &middot; uptime {uptime}</p>
</div></body></html>"""


def fmt_hms(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def render(store, ring, paused=False):
    st = store.snapshot()
    logs = "\n".join(ring.lines())
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
        tags = ", ".join(st.get("request_tags") or []) or "subs:en"
        parts.append(
            f'<div class="title">{st["queue"]} scene(s) queued</div>'
            f'<div class="meta">Tag scenes in Stash with {html.escape(tags)} '
            f'to add work{wait}</div>'
        )

    parts.append(
        '<div style="margin-top:16px">'
        '<form method="post" action="/poll"><button>Poll now</button></form>'
        + ('<form method="post" action="/resume"><button>Resume</button></form>'
           if paused else
           '<form method="post" action="/pause"><button>Pause</button></form>')
        + '</div>'
    )
    if st.get("poll_note"):
        parts.append(f'<div class="note">{html.escape(st["poll_note"])}</div>')
    parts.append('</div>')

    if st["completed"]:
        rows = "".join(
            f'<tr><td>{time.strftime("%H:%M", time.localtime(c["at"]))}</td>'
            f'<td>{html.escape(c["what"])}</td></tr>'
            for c in reversed(st["completed"])
        )
        parts.append(f'<div class="card"><h2>Recently finished</h2><table>{rows}</table></div>')

    return PAGE.format(
        body="".join(parts),
        logs=html.escape(logs) or "(nothing yet)",
        uptime=fmt_hms(time.time() - st["started"]),
        version=html.escape(__version__),
    )


def make_handler(store, ring, control):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            path = self.path.rstrip("/")
            if path in ("", "/status"):
                body = render(store, ring, paused=control.paused).encode()
                self._send(200, body, "text/html; charset=utf-8")
            elif path == "/json":
                snap = store.snapshot()
                snap["paused"] = control.paused
                self._send(200, json.dumps(snap, default=str).encode(), "application/json")
            else:
                self.send_error(404)

        def do_POST(self):
            path = self.path.rstrip("/")
            # A cross-site POST could make someone else's browser drive this
            # worker; the endpoints are unauthenticated by design.
            site = self.headers.get("Sec-Fetch-Site")
            if site not in (None, "same-origin", "none"):
                self.send_error(403, "cross-site requests are not accepted")
                return
            if path == "/poll":
                note = control.request_poll()
            elif path == "/pause":
                note = control.pause()
            elif path == "/resume":
                note = control.resume()
            else:
                self.send_error(404)
                return
            store.update(poll_note=note)
            if "application/json" in (self.headers.get("Accept") or ""):
                self._send(200, json.dumps({"ok": True, "note": note}).encode(),
                           "application/json")
            else:
                # 303 so the meta-refresh on the status page cannot re-fire it.
                self.send_response(303)
                self.send_header("Location", "/")
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


def serve(store, ring, control, host, port):
    srv = ThreadingHTTPServer((host, port), make_handler(store, ring, control))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
