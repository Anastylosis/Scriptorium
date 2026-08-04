import pytest

from scriptorium import outcomes


def t(action, **kw):
    return outcomes.Target(kw.pop("lang", "en"), action, **kw)


# -- what counts as a failed scene -----------------------------------------

@pytest.mark.parametrize("action", [outcomes.WRITTEN, outcomes.SKIPPED,
                                    outcomes.NO_SPEECH, outcomes.DRY_RUN])
def test_a_scene_that_did_what_it_could_is_ok(action):
    assert outcomes.Scene(targets=(t(action),)).ok


@pytest.mark.parametrize("action", [outcomes.UNSUPPORTED, outcomes.ERROR])
def test_a_request_that_could_not_be_honoured_fails_the_scene(action):
    # This is the behaviour change: these used to end as subs:done, so the
    # request vanished with only a log line to show for it.
    assert not outcomes.Scene(targets=(t(action),)).ok


def test_one_bad_target_fails_the_whole_scene():
    scene = outcomes.Scene(targets=(t(outcomes.WRITTEN),
                                    t(outcomes.UNSUPPORTED, lang="es")))
    assert not scene.ok


def test_a_scene_that_never_started_is_a_failure():
    assert not outcomes.failed("path not visible").ok


def test_a_scene_with_nothing_requested_is_not_a_failure():
    # Its request tags were removed while it sat in the queue; there is
    # nothing wrong with it.
    assert outcomes.Scene().ok


# -- when Stash needs telling ----------------------------------------------

def test_a_new_caption_needs_a_scan():
    assert outcomes.Scene(targets=(t(outcomes.WRITTEN, new_caption=True),)).needs_scan


def test_rewriting_a_known_caption_does_not():
    assert not outcomes.Scene(
        targets=(t(outcomes.WRITTEN, new_caption=False),)).needs_scan


def test_a_skip_never_needs_a_scan():
    assert not outcomes.Scene(targets=(t(outcomes.SKIPPED),)).needs_scan


def test_a_salvaged_transcript_still_needs_registering():
    # Translation failed, so the target errored — but the source-language
    # transcript was written and Stash has to be told, or the file sits on
    # disk where nothing will ever see it.
    scene = outcomes.Scene(targets=(t(outcomes.ERROR, new_caption=True),))
    assert not scene.ok
    assert scene.needs_scan


def test_an_unproducible_target_that_salvaged_a_transcript_is_scanned():
    scene = outcomes.Scene(targets=(t(outcomes.UNSUPPORTED, new_caption=True),))
    assert not scene.ok
    assert scene.needs_scan


def test_a_failure_that_wrote_nothing_is_not_scanned():
    assert not outcomes.Scene(targets=(t(outcomes.ERROR),)).needs_scan


def test_a_written_target_alongside_a_failed_one_still_scans():
    scene = outcomes.Scene(targets=(
        t(outcomes.WRITTEN, lang="en", new_caption=True),
        t(outcomes.UNSUPPORTED, lang="es")))
    assert scene.needs_scan
    assert not scene.ok


def test_a_failed_scene_reports_no_scan():
    assert not outcomes.failed("boom").needs_scan


# -- the log line ----------------------------------------------------------

def test_summary_names_every_target():
    scene = outcomes.Scene(targets=(
        outcomes.Target("en", outcomes.WRITTEN, "412 cues"),
        outcomes.Target("es", outcomes.UNSUPPORTED, "OLLAMA_URL is unset")))
    s = scene.summary()
    assert "en: written (412 cues)" in s
    assert "es: unsupported (OLLAMA_URL is unset)" in s


def test_summary_of_a_fatal_scene_is_the_reason():
    assert outcomes.failed("no file attached").summary() == "no file attached"


def test_summary_with_nothing_requested():
    assert outcomes.Scene().summary() == "nothing requested"
