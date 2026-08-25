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


# The page is one ruled leaf, not a stack of cards: a continuous margin rule
# with labels hanging in the margin and the record in the text block. Colour is
# two inks with jobs rather than two brand accents — lapis is the machine's own
# voice (state, structure, links), rubric is reserved for the one thing the
# operator must not miss (the write head, the target in hand, a failure).
#
# No webfont. The page hard-refreshes every 5s, so a blocking request to a font
# host would stall every one of those on a box with no route out.
CSS = """
  :root {
    --ink:#131A22; --vellum:#E8E2D6; --faded:#7C8894;
    --rule:#232E39; --ruling:#2E3B49; --rubric:#C8443A; --lapis:#6E93C8; --amber:#C9A25E;
    --serif:Palatino,"Palatino Linotype","Iowan Old Style","Book Antiqua",
            "TeX Gyre Pagella","URW Palladio L",P052,Georgia,serif;
    --mono:ui-monospace,SFMono-Regular,"SF Mono","JetBrains Mono",Menlo,
           Consolas,monospace;
  }
  * { box-sizing:border-box; }
  body { background:var(--ink); color:var(--vellum); margin:0;
         font:13px/1.6 var(--mono); -webkit-font-smoothing:antialiased; }
  .leaf { max-width:940px; margin:0 auto; padding:40px 24px 72px; }
  a { color:var(--lapis); text-decoration:none;
      border-bottom:1px solid rgba(110,147,200,.3); }
  a:hover { border-bottom-color:var(--lapis); }
  :focus-visible { outline:2px solid var(--lapis); outline-offset:3px; }

  /* masthead */
  .masthead { display:flex; align-items:baseline; justify-content:space-between;
              gap:16px; padding-bottom:14px; border-bottom:1px solid var(--rule); }
  h1 { font:400 25px/1 var(--serif); letter-spacing:.055em; margin:0; }
  .ver { color:var(--faded); font-size:11.5px; letter-spacing:.1em;
         text-transform:uppercase; margin:0; }

  /* the ruled grid: marginalia | text block */
  .row { display:grid; grid-template-columns:128px 1fr; }
  .margin { text-align:right; padding:26px 22px 0 0; }
  .label { font:italic 400 14px/1.3 var(--serif); color:var(--faded);
           letter-spacing:.01em; }
  .block { border-left:1px solid var(--rule); padding:26px 0 30px 24px;
           min-width:0; }
  .row + .row .block { border-top:1px solid var(--rule); }

  /* what it is doing */
  .state { font-size:11.5px; letter-spacing:.19em; text-transform:uppercase; }
  .state-working { color:var(--lapis); }
  .state-idle    { color:var(--faded); }
  .state-paused, .state-starting { color:var(--amber); }
  .state-error   { color:var(--rubric); }
  .work { font:400 27px/1.25 var(--serif); color:var(--vellum);
          margin:9px 0 5px; overflow-wrap:anywhere; }
  .meta { color:var(--faded); font-size:12px; margin:0; overflow-wrap:anywhere; }

  /* the signature: a line being written. Inked behind the nib, ruled ahead. */
  .writing { display:flex; align-items:center; gap:14px; margin:24px 0 9px; }
  .tc { color:var(--vellum); font-size:12.5px; font-variant-numeric:tabular-nums;
        flex:none; }
  .ruling { position:relative; flex:1 1 auto; height:1px; background:var(--ruling); }
  .inked { position:absolute; left:0; top:0; height:1px; background:var(--vellum);
           transition:width .5s ease; }
  .nib { position:absolute; right:0; top:-6px; width:2px; height:13px;
         background:var(--rubric); }
  /* Period divides the 5s refresh, so a reload lands on the same phase. */
  @keyframes breathe { 0%,100% { opacity:1 } 50% { opacity:.4 } }
  .nib { animation:breathe 2.5s ease-in-out infinite; }

  /* facts */
  .facts { display:grid; grid-template-columns:auto 1fr; gap:7px 18px;
           margin:20px 0 0; }
  .facts dt { color:var(--faded); font-size:11.5px; letter-spacing:.11em;
              text-transform:uppercase; padding-top:2px; }
  .facts dd { margin:0; color:var(--vellum); }
  .tally { font:400 18px/1 var(--serif); }
  .lang { color:var(--faded); }
  .lang-now { color:var(--rubric); }

  /* controls */
  .controls { display:flex; flex-wrap:wrap; gap:9px; margin-top:26px; }
  form { display:contents; }
  button { background:none; color:var(--vellum); border:1px solid var(--rule);
           padding:7px 15px; font:inherit; font-size:11.5px; letter-spacing:.11em;
           text-transform:uppercase; cursor:pointer; transition:border-color .18s; }
  button:hover { border-color:var(--faded); }
  .note { color:var(--lapis); font-size:12px; margin:14px 0 0; }

  /* finished */
  .ledger { list-style:none; margin:0; padding:0; }
  .ledger li { display:grid; grid-template-columns:52px 1fr auto; gap:16px;
               padding:5px 0; border-bottom:1px solid var(--rule); }
  .ledger li:last-child { border-bottom:0; }
  .ledger time { color:var(--faded); font-variant-numeric:tabular-nums; }
  .what { color:var(--vellum); overflow-wrap:anywhere; }
  .count { color:var(--faded); white-space:nowrap; }
  .failed .what { color:var(--rubric); }

  /* log — reversed flex pins the scroll to the newest line without script */
  .logwrap { display:flex; flex-direction:column-reverse; overflow:auto;
             max-height:420px; }
  pre { margin:0; color:var(--faded); font-size:12px; line-height:1.65;
        white-space:pre-wrap; overflow-wrap:anywhere; }

  footer { color:var(--faded); font-size:11.5px; margin-top:32px;
           padding-top:14px; border-top:1px solid var(--rule); }

  @media (max-width:640px) {
    .leaf { padding:28px 18px 56px; }
    .row { grid-template-columns:1fr; }
    .margin { text-align:left; padding:22px 0 0; }
    .block { border-left:0; padding:6px 0 24px; }
    .row + .row .block { border-top:0; }
    .row + .row .margin { border-top:1px solid var(--rule); }
    .work { font-size:23px; }
  }
  @media (prefers-reduced-motion:reduce) {
    * { animation:none !important; transition:none !important; }
  }
"""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Scriptorium</title>
<meta http-equiv="refresh" content="5">
<style>{css}</style></head><body>
<div class="leaf">
<header class="masthead"><h1>Scriptorium</h1><p class="ver">{version}</p></header>
{body}
<div class="row"><div class="margin"><span class="label">log</span></div>
<div class="block"><div class="logwrap"><pre>{logs}</pre></div></div></div>
<footer>Refreshes every 5s &middot; up {uptime} &middot;
<a href="https://github.com/Anastylosis/Scriptorium">github.com/Anastylosis/Scriptorium</a></footer>
</div></body></html>"""


def fmt_hms(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _row(label, block):
    return (f'<div class="row"><div class="margin">'
            f'<span class="label">{label}</span></div>'
            f'<div class="block">{block}</div></div>')


def _writing_line(position, duration, pct):
    """The progress indicator: inked behind the write head, ruled ahead of it."""
    return (f'<div class="writing">'
            f'<span class="tc">{fmt_hms(position)}</span>'
            f'<span class="ruling"><span class="inked" style="width:{pct:.1f}%">'
            f'<i class="nib"></i></span></span>'
            f'<span class="tc">{fmt_hms(duration)}</span></div>')


def _working(st):
    pct = (st["position"] / st["duration"] * 100) if st["duration"] else 0
    pct = max(0, min(100, pct))
    elapsed = time.time() - (st["started_scene"] or time.time())
    eta = (elapsed / pct * (100 - pct)) if pct > 2 else 0
    speed = (st["position"] / elapsed) if elapsed > 0 else 0

    left = f' &middot; {fmt_hms(eta)} left' if eta else ''
    out = ['<div class="state state-working">working</div>']
    out.append(f'<h2 class="work">{html.escape(st["scene"])}</h2>')
    out.append(f'<p class="meta">scene {html.escape(str(st["scene_id"]))}'
               f' &middot; {html.escape(st["stage"] or "")}</p>')
    out.append(_writing_line(st["position"], st["duration"], pct))
    out.append(f'<p class="meta">{pct:.0f}% &middot; {speed:.1f}&times;'
               f' realtime{left}</p>')
    out.append('<dl class="facts">')
    if st["source_lang"]:
        conf = (f' <span class="lang">{st["lang_confidence"]:.0%} confident</span>'
                if st["lang_confidence"] else '')
        out.append(f'<dt>heard</dt><dd>{html.escape(st["source_lang"])}{conf}</dd>')
    if st["targets"]:
        # The language in hand is rubricated: it is the one fact here that
        # changes while you are looking at the page.
        marks = " ".join(
            f'<span class="{"lang-now" if t == st["target"] else "lang"}">'
            f'{html.escape(t)}</span>'
            for t in st["targets"])
        out.append(f'<dt>writing</dt><dd>{marks}</dd>')
    out.append(f'<dt>waiting</dt><dd><span class="tally">{st["queue"]}</span> '
               f'{"scene" if st["queue"] == 1 else "scenes"}</dd>')
    out.append('</dl>')
    return out


def _resting(st, status):
    queue = st["queue"]
    names = st.get("request_tags") or ["subs:en"]
    tags = html.escape(", ".join(names))
    if len(names) > 1:
        tags = f"one of {tags}"

    if queue:
        head = (f'<h2 class="work"><span class="tally">{queue}</span> '
                f'{"scene" if queue == 1 else "scenes"} waiting</h2>')
    else:
        head = '<h2 class="work">Nothing waiting</h2>'

    # A paused worker does not start on the next poll, so it may not say so.
    if status == "paused":
        note = "Nothing starts until you resume."
    else:
        note = ("Work starts on the next poll." if queue else
                f"Tag a scene in Stash with {tags} and it will be picked up.")
        nxt = st.get("next_poll")
        if nxt:
            note += f" Next poll in {max(0, int(nxt - time.time()))}s."
    return [f'<div class="state state-{status}">{status}</div>', head,
            f'<p class="meta">{note}</p>']


def _controls(paused):
    swap = ('<form method="post" action="/resume"><button>Resume</button></form>'
            if paused else
            '<form method="post" action="/pause"><button>Pause</button></form>')
    return ('<div class="controls">'
            '<form method="post" action="/poll"><button>Poll now</button></form>'
            + swap + '</div>')


def _ledger(entries):
    rows = []
    for c in reversed(entries):
        what = c["what"]
        # write() emits "name.en.srt — 412 cues"; the run loop emits a bare
        # FAILED line. Both land here, and only one of them is good news.
        name, _, count = what.partition(" — ")
        failed = ' class="failed"' if what.startswith("FAILED") else ''
        rows.append(
            f'<li{failed}><time>{time.strftime("%H:%M", time.localtime(c["at"]))}'
            f'</time><span class="what">{html.escape(name)}</span>'
            f'<span class="count">{html.escape(count)}</span></li>')
    return f'<ol class="ledger">{"".join(rows)}</ol>'


def render(store, ring, paused=False):
    st = store.snapshot()
    status = st["status"]

    if status == "working" and st["scene"]:
        block = _working(st)
    else:
        block = _resting(st, status)
    block.append(_controls(paused))
    if st.get("poll_note"):
        block.append(f'<p class="note">{html.escape(st["poll_note"])}</p>')

    body = _row("state", "".join(block))
    if st["completed"]:
        body += _row("finished", _ledger(st["completed"]))

    return PAGE.format(
        css=CSS,
        body=body,
        logs=html.escape("\n".join(ring.lines())) or "(nothing yet)",
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
