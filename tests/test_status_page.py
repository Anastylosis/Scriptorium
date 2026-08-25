"""The status page: what it says, and what the control endpoints accept.

The page is the only thing most people ever see of this worker, and it is
assembled by hand from strings, so the things pinned here are the ones a
careless edit breaks silently: the arithmetic behind the write head, the
copy that would otherwise lie about a paused worker, escaping, and the
same-origin check standing in front of endpoints that drive the queue.
"""

import http.client
import json
import time

import pytest

from scriptorium import status


class Ring:
    def __init__(self, lines=()):
        self._lines = list(lines)

    def lines(self):
        return self._lines


class Control:
    """The three signals the handler reaches for, and nothing else."""

    def __init__(self):
        self.paused = False
        self.polls = 0

    def request_poll(self):
        self.polls += 1
        return "polling now"

    def pause(self):
        self.paused = True
        return "pausing after the current scene"

    def resume(self):
        self.paused = False
        return "resumed"


WORKING = {"status": "working", "scene": "RCT-299", "scene_id": 62552,
           "stage": "transcribing ja", "source_lang": "ja",
           "lang_confidence": 0.99, "duration": 1000.0, "position": 500.0,
           "target": "ja", "targets": ["en", "ja"], "queue": 7}


def page(lines=(), paused=False, **state):
    store = status.Store()
    store.update(**state)
    return status.render(store, Ring(lines), paused=paused)


def outside_css(html):
    """The page with the stylesheet cut out."""
    return html.split("<style>")[0] + html.split("</style>")[1]


# -- fmt_hms ---------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00"), (59, "0:59"), (60, "1:00"), (3661, "1:01:01"),
])
def test_fmt_hms(seconds, expected):
    assert status.fmt_hms(seconds) == expected


# -- at work ---------------------------------------------------------------

def test_the_working_page_names_the_scene_and_what_it_is_doing():
    html = page(**WORKING)
    assert "RCT-299" in html
    assert "scene 62552" in html
    assert "transcribing ja" in html


def test_the_write_head_sits_at_the_elapsed_fraction():
    assert "width:50.0%" in page(**WORKING)


def test_a_position_past_the_end_does_not_run_off_the_line():
    # Whisper reports against decoded audio, which a container format can
    # overstate; the head has to stop at the end of the line either way.
    assert "width:100.0%" in page(**{**WORKING, "position": 4000.0})


def test_an_unknown_duration_does_not_divide_by_zero():
    html = page(**{**WORKING, "duration": 0.0, "position": 0.0})
    assert "width:0.0%" in html


def test_the_language_in_hand_is_marked_and_the_others_are_not():
    # The one fact on the page that changes while you are looking at it.
    html = page(**WORKING)
    assert '<span class="lang-now">ja</span>' in html
    assert '<span class="lang">en</span>' in html


def test_the_source_language_is_shown_with_its_confidence():
    assert "99% confident" in page(**WORKING)


def test_a_scene_title_cannot_smuggle_markup_onto_the_page():
    html = page(**{**WORKING, "scene": '<script>alert("x")</script> & co'})
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_the_queue_is_counted_in_words_that_agree():
    assert ">1</span> scene waiting" in page(status="idle", queue=1)
    assert ">2</span> scenes waiting" in page(status="idle", queue=2)


# -- at rest ---------------------------------------------------------------

def test_an_empty_queue_says_how_to_add_work():
    html = page(status="idle", queue=0, request_tags=["subs:en"])
    assert "Nothing waiting" in html
    assert "subs:en" in html


def test_several_request_tags_are_offered_as_alternatives():
    # "with subs:en, subs:ja" reads as needing both.
    html = page(status="idle", queue=0, request_tags=["subs:en", "subs:ja"])
    assert "one of subs:en, subs:ja" in html


def test_a_single_request_tag_is_not_offered_as_a_choice():
    assert "one of" not in page(status="idle", queue=0, request_tags=["subs:en"])


def test_a_paused_worker_does_not_claim_work_starts_on_the_next_poll():
    # It was saying exactly that, which is false: nothing runs until resume.
    html = page(status="paused", queue=1746, paused=True)
    assert "next poll" not in html
    assert "resume" in html.lower()


def test_the_wait_until_the_next_poll_is_shown_when_there_is_one():
    html = page(status="idle", queue=0, next_poll=time.time() + 42)
    assert "Next poll in 4" in html


def test_a_past_poll_time_does_not_count_backwards():
    html = page(status="idle", queue=0, next_poll=time.time() - 99)
    assert "Next poll in 0s" in html


def test_the_controls_offer_the_action_that_is_available():
    assert "Pause</button>" in page(status="idle", paused=False)
    assert "Resume</button>" in page(status="idle", paused=True)


def test_the_note_from_the_last_control_press_is_shown():
    assert "pausing after" in page(status="idle", poll_note="pausing after this")


# -- the ledger ------------------------------------------------------------

def ledger(*entries):
    store = status.Store()
    store.update(status="idle")
    now = time.time()
    for i, what in enumerate(entries):
        store.add_completed(what)
        store._completed[-1]["at"] = now + i
    return status.render(store, Ring())


def test_finished_files_are_listed_newest_first():
    html = ledger("first.en.srt — 1 cues", "second.en.srt — 2 cues")
    assert html.index("second.en.srt") < html.index("first.en.srt")


def test_the_cue_count_is_split_off_into_its_own_column():
    html = ledger("clip.en.srt — 412 cues")
    assert '<span class="what">clip.en.srt</span>' in html
    assert '<span class="count">412 cues</span>' in html


def test_a_failed_scene_is_marked_rather_than_reading_as_a_success():
    # It used to be the same grey as every finished file above it.
    assert '<li class="failed">' in ledger("FAILED scene 61981: RuntimeError")


def test_an_entry_with_no_count_still_renders():
    html = ledger("something happened")
    assert '<span class="what">something happened</span>' in html


def test_a_filename_cannot_smuggle_markup_onto_the_page():
    assert "<b>" not in ledger("<b>x</b>.en.srt — 1 cues")


# -- the whole page --------------------------------------------------------

def test_the_template_leaves_no_unfilled_placeholders():
    # PAGE is built with str.format; a stray brace in the markup would show
    # up as a literal on the page or blow up the format call.
    html = outside_css(page(**WORKING))
    assert "{" not in html and "}" not in html


def test_an_empty_log_says_so():
    assert "(nothing yet)" in page(status="idle")


def test_the_log_is_escaped():
    html = page(lines=["<b>not markup</b>"], status="idle")
    assert "<b>not markup</b>" not in html
    assert "&lt;b&gt;" in html


def test_the_page_declares_a_viewport():
    # Without it the phone renders it at desktop width and scales down.
    assert 'name="viewport"' in page(status="idle")


# -- the endpoints ---------------------------------------------------------

@pytest.fixture
def server():
    store = status.Store()
    store.update(**WORKING)
    control = Control()
    srv = status.serve(store, Ring(["a line"]), control, "127.0.0.1", 0)
    yield srv.server_address[1], control, store
    srv.shutdown()
    srv.server_close()


def call(port, method, path, headers=None, follow=False):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, headers=headers or {})
    r = conn.getresponse()
    body = r.read()
    out = (r.status, r.getheader("Location"), body)
    conn.close()
    return out


def test_the_page_is_served_at_the_root_and_at_status(server):
    port, _, _ = server
    for path in ("/", "/status"):
        code, _, body = call(port, "GET", path)
        assert code == 200
        assert b"Scriptorium" in body


def test_the_json_view_carries_the_paused_flag(server):
    port, control, _ = server
    control.paused = True
    code, _, body = call(port, "GET", "/json")
    assert code == 200
    snap = json.loads(body)
    assert snap["paused"] is True
    assert snap["scene"] == "RCT-299"


def test_an_unknown_path_is_a_404(server):
    port, _, _ = server
    assert call(port, "GET", "/nope")[0] == 404


def test_poll_reaches_the_worker(server):
    port, control, _ = server
    assert call(port, "POST", "/poll")[0] == 303
    assert control.polls == 1


def test_pause_and_resume_reach_the_worker(server):
    port, control, _ = server
    call(port, "POST", "/pause")
    assert control.paused is True
    call(port, "POST", "/resume")
    assert control.paused is False


def test_a_control_press_redirects_rather_than_answering_with_the_page(server):
    # The page carries a meta-refresh; answering the POST with it directly
    # would have the browser re-fire the POST every five seconds.
    port, _, _ = server
    code, location, _ = call(port, "POST", "/poll")
    assert (code, location) == (303, "/")


def test_a_cross_site_post_cannot_drive_the_worker(server):
    # The endpoints are unauthenticated by design, so this header is the only
    # thing between them and any page the operator happens to have open.
    port, control, _ = server
    code, _, _ = call(port, "POST", "/pause",
                      headers={"Sec-Fetch-Site": "cross-site"})
    assert code == 403
    assert control.paused is False


def test_a_same_origin_post_is_accepted(server):
    port, control, _ = server
    code, _, _ = call(port, "POST", "/pause",
                      headers={"Sec-Fetch-Site": "same-origin"})
    assert code == 303
    assert control.paused is True


def test_a_client_asking_for_json_gets_the_note_back(server):
    port, _, _ = server
    code, _, body = call(port, "POST", "/poll",
                         headers={"Accept": "application/json"})
    assert code == 200
    assert json.loads(body) == {"ok": True, "note": "polling now"}


def test_an_unknown_control_is_a_404(server):
    port, _, _ = server
    assert call(port, "POST", "/launch")[0] == 404
