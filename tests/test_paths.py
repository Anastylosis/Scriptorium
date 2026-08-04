from pathlib import Path

from scriptorium.paths import PathMapper


def test_identity_mapping_is_a_no_op():
    m = PathMapper("/data", "/data")
    assert m.to_local("/data/a.mp4") == Path("/data/a.mp4")


def test_maps_the_prefix():
    m = PathMapper("/data", "/media")
    assert m.to_local("/data/a.mp4") == Path("/media/a.mp4")


def test_only_matches_at_a_path_boundary():
    # A plain str.replace rewrote /mnt/data/a.mp4 to /mnt/media/a.mp4,
    # because "/data" occurs in the middle of it.
    m = PathMapper("/data", "/media")
    assert m.to_local("/mnt/data/a.mp4") == Path("/mnt/data/a.mp4")
    assert m.to_local("/database/a.mp4") == Path("/database/a.mp4")
    assert m.to_local("/data2/a.mp4") == Path("/data2/a.mp4")


def test_maps_the_bare_prefix():
    m = PathMapper("/data", "/media")
    assert m.to_local("/data") == Path("/media")


def test_trailing_slashes_are_tolerated():
    m = PathMapper("/data/", "/media/")
    assert m.to_local("/data/a.mp4") == Path("/media/a.mp4")


def test_nested_directories_are_preserved():
    m = PathMapper("/data", "/media")
    assert m.to_local("/data/x/y/z.mp4") == Path("/media/x/y/z.mp4")


def test_to_stash_is_the_inverse():
    m = PathMapper("/data", "/media")
    assert m.to_stash(Path("/media/x/a.mp4")) == "/data/x/a.mp4"
    assert m.to_stash("/media") == "/data"


def test_to_stash_leaves_uncovered_paths_alone():
    m = PathMapper("/data", "/media")
    assert m.to_stash("/elsewhere/a.mp4") == "/elsewhere/a.mp4"
