"""How the queue is asked for.

Path order is what lets the worker tell it has finished with a directory
early enough for the rescan to matter. It is an optimisation, so a Stash that
will not sort scenes by path has to degrade rather than stop the poll.
"""

import re

import pytest

from scriptorium.stash import Client, StashError


class Recorder(Client):
    """A client that answers queue queries without a Stash, and refuses to
    sort by any of `rejects` the way an older Stash would."""

    def __init__(self, rejects=()):
        super().__init__("http://stash")
        self.rejects = set(rejects)
        self.sorts = []

    def execute(self, query, variables=None):
        sort = re.search(r'sort: "(\w+)"', query).group(1)
        self.sorts.append(sort)
        if sort in self.rejects:
            raise StashError("Stash GraphQL error: unknown sort")
        return {"findScenes": {"count": 0, "scenes": []}}


def test_the_queue_is_asked_for_in_path_order():
    c = Recorder()
    assert c.find_tagged_scenes(["1"]) == []
    assert c.sorts == ["path"]


def test_a_stash_that_will_not_sort_by_path_still_gets_its_queue():
    c = Recorder(rejects=["path"])
    assert c.find_tagged_scenes(["1"]) == []
    assert c.sorts == ["path", "id"]


def test_the_fallback_is_remembered():
    # Otherwise every poll pays for a rejected query, forever.
    c = Recorder(rejects=["path"])
    c.find_tagged_scenes(["1"])
    c.find_tagged_scenes(["1"])
    assert c.sorts == ["path", "id", "id"]


def test_a_stash_that_is_simply_down_is_not_mistaken_for_an_old_one():
    c = Recorder(rejects=["path", "id"])
    with pytest.raises(StashError):
        c.find_tagged_scenes(["1"])
    assert c.sorts == ["path", "id"]
