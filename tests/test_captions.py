from pathlib import Path

from stash_subs import captions
from stash_subs.stash import Client, StashError

VIDEO = Path("/data/clip.mp4")


def scene(*caps):
    return {"captions": [{"language_code": c, "caption_type": t} for c, t in caps]}


# -- registered ------------------------------------------------------------

def test_registered_matches_the_same_code():
    assert captions.registered(scene(("en", "srt")), "en")


def test_registered_matches_an_equivalent_code():
    # A hand-placed foo.eng.srt already covers English.
    assert captions.registered(scene(("eng", "srt")), "en")
    assert captions.registered(scene(("en", "srt")), "eng")


def test_registered_does_not_match_another_language():
    assert not captions.registered(scene(("es", "srt")), "en")


def test_registered_is_extension_specific():
    # A VTT does not mean the SRT exists.
    assert not captions.registered(scene(("en", "vtt")), "en", ext="srt")
    assert captions.registered(scene(("en", "vtt")), "en", ext="vtt")


def test_stash_unknown_language_marker_never_counts():
    # Stash files a caption with no language suffix as "00"; treating that as
    # a match would suppress every language on the scene.
    assert not captions.registered(scene(("00", "srt")), "en")
    assert not captions.registered(scene(("", "srt")), "en")


def test_a_scene_with_no_captions_key_is_handled():
    assert not captions.registered({}, "en")
    assert not captions.registered({"captions": None}, "en")


# -- existing_file ---------------------------------------------------------

def test_existing_file_finds_an_equivalent_spelling(tmp_path):
    video = tmp_path / "clip.mp4"
    (tmp_path / "clip.eng.srt").write_text("x", encoding="utf-8")
    found = captions.existing_file(video, scene(("eng", "srt")), "en")
    assert found is not None and found.name == "clip.eng.srt"


def test_a_registered_caption_whose_file_was_deleted_does_not_count(tmp_path):
    # Stash still lists it, but the user deleted it off disk, so it has to be
    # regenerated rather than silently skipped forever.
    video = tmp_path / "clip.mp4"
    assert captions.existing_file(video, scene(("eng", "srt")), "en") is None


def test_existing_file_ignores_other_languages(tmp_path):
    video = tmp_path / "clip.mp4"
    (tmp_path / "clip.es.srt").write_text("x", encoding="utf-8")
    assert captions.existing_file(video, scene(("es", "srt")), "en") is None


def test_existing_file_ignores_the_unknown_marker(tmp_path):
    video = tmp_path / "clip.mp4"
    (tmp_path / "clip.00.srt").write_text("x", encoding="utf-8")
    assert captions.existing_file(video, scene(("00", "srt")), "en") is None


# -- the capability probe --------------------------------------------------

class Recorder(Client):
    def __init__(self, fail_on_captions):
        super().__init__("http://x")
        self.fail_on_captions = fail_on_captions
        self.queries = []

    def execute(self, query, variables=None):
        self.queries.append(query)
        if "captions" in query and self.fail_on_captions:
            raise StashError('Cannot query field "captions" on type "Scene"')
        if "findScenes" in query:
            return {"findScenes": {"count": 0, "scenes": []}}
        return {}


def test_probe_enables_captions_when_supported():
    c = Recorder(fail_on_captions=False)
    assert c.probe_captions() is True
    c.find_tagged_scenes(["1"])
    assert "captions" in c.queries[-1]


def test_probe_falls_back_when_the_field_is_missing():
    # Asking for an unknown field fails the whole query, so an older Stash
    # would be unusable rather than merely missing this optimisation.
    c = Recorder(fail_on_captions=True)
    assert c.probe_captions() is False
    c.find_tagged_scenes(["1"])
    assert "captions" not in c.queries[-1]


def test_captions_are_off_before_probing():
    assert Client("http://x").supports_captions is False
