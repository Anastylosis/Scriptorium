import logging

import pytest

from stash_subs import config, status, tags
from stash_subs.worker import Worker


class FakeClient:
    """A Stash whose tag list the test controls."""

    def __init__(self, names, supports_regex=True):
        self._tags = [{"id": str(i + 1), "name": n} for i, n in enumerate(names)]
        self.supports_regex = supports_regex
        self.created = []
        self.updates = []

    def find_tags_matching(self, pattern):
        if not self.supports_regex:
            raise RuntimeError("MATCHES_REGEX not supported by this Stash")
        return [t for t in self._tags if t["name"].lower().startswith("subs:")]

    def all_tags(self):
        return list(self._tags)

    def find_or_create_tag(self, name):
        for t in self._tags:
            if t["name"].lower() == name.lower():
                return t["id"]
        self.created.append(name)
        self._tags.append({"id": str(len(self._tags) + 1), "name": name})
        return self._tags[-1]["id"]

    def add(self, name):
        self._tags.append({"id": str(len(self._tags) + 1), "name": name})
        return self._tags[-1]["id"]

    def scene_tags(self, scene_id):
        return getattr(self, "_scene_tags", [])

    def set_scene_tags(self, scene_id, tag_ids):
        self.updates.append((scene_id, list(tag_ids)))


def plan_for(names, **env):
    client = FakeClient(names)
    cfg = config.from_env(env).tags
    done, failed = tags.bootstrap(client, cfg)
    return client, tags.discover(client, cfg, done, failed)


def test_discovers_any_language_tag():
    _, plan = plan_for(["subs:en", "subs:fr", "subs:ja", "subs:auto"])
    assert plan.names() == ["subs:auto", "subs:en", "subs:fr", "subs:ja"]
    assert {t.lang for t in plan.requests.values()} == {"en", "fr", "ja", "auto"}


def test_three_letter_tags_are_normalised():
    # subs:en is always created at startup, so it is in the plan too; the
    # point here is that subs:por resolves to pt and subs:eng to en.
    _, plan = plan_for(["subs:eng", "subs:por"])
    assert sorted(set(t.lang for t in plan.requests.values())) == ["en", "pt"]


def test_two_tags_naming_one_language_are_transcribed_once():
    client, plan = plan_for(["subs:eng"])          # plus subs:en from bootstrap
    w = worker_with(plan, client)
    scene = {"tags": [{"id": tid, "name": t.name}
                      for tid, t in plan.requests.items()]}
    assert w.targets_for(scene) == ["en"]


def test_rejects_regional_subtags_and_nonsense():
    _, plan = plan_for(["subs:en", "subs:pt-BR", "subs:xx"])
    assert [t.lang for t in plan.requests.values()] == ["en"]
    assert "regional subtag" in plan.rejected["subs:pt-BR"]
    assert "not an ISO 639" in plan.rejected["subs:xx"]


def test_done_and_failed_are_not_treated_as_requests():
    _, plan = plan_for(["subs:en", "subs:done", "subs:failed"])
    assert plan.names() == ["subs:en"]
    assert plan.rejected == {}


def test_ignore_list_is_honoured():
    _, plan = plan_for(["subs:en", "subs:fr"], IGNORE_TAGS="subs:fr")
    assert plan.names() == ["subs:en"]


def test_non_subs_tags_are_untouched():
    _, plan = plan_for(["subs:en", "4k", "favourite"])
    assert plan.names() == ["subs:en"]
    assert plan.rejected == {}


def test_falls_back_when_the_server_rejects_regex():
    client = FakeClient(["subs:en", "subs:fr", "4k"], supports_regex=False)
    cfg = config.from_env({}).tags
    done, failed = tags.bootstrap(client, cfg)
    plan = tags.discover(client, cfg, done, failed)
    assert plan.names() == ["subs:en", "subs:fr"]


def test_only_subs_en_is_created_at_startup():
    client = FakeClient([])
    cfg = config.from_env({}).tags
    tags.bootstrap(client, cfg)
    assert client.created == ["subs:en", "subs:done", "subs:failed"]
    assert "subs:es" not in client.created


def test_a_new_tag_is_picked_up_on_the_next_poll():
    client, first = plan_for(["subs:en"])
    cfg = config.from_env({}).tags
    client.add("subs:de")
    second = tags.discover(client, cfg, first.done_id, first.failed_id,
                           previous=first)
    assert second.names() == ["subs:de", "subs:en"]


def test_rejections_are_logged_once_not_every_poll(caplog):
    client, first = plan_for(["subs:en", "subs:pt-BR"])
    cfg = config.from_env({}).tags
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        p = first
        for _ in range(3):
            p = tags.discover(client, cfg, p.done_id, p.failed_id, previous=p)
    assert [r for r in caplog.records if "pt-BR" in r.getMessage()] == []


def test_a_newly_appearing_rejection_is_logged(caplog):
    client, first = plan_for(["subs:en"])
    cfg = config.from_env({}).tags
    client.add("subs:pt-BR")
    with caplog.at_level(logging.WARNING):
        tags.discover(client, cfg, first.done_id, first.failed_id, previous=first)
    assert any("pt-BR" in r.getMessage() for r in caplog.records)


# -- REQUEST_TAGS backward compatibility ----------------------------------

def test_discovery_is_on_when_request_tags_is_unset():
    on, note = tags.discovery_enabled(config.from_env({}).tags)
    assert on and note is None


def test_the_shipped_default_does_not_disable_discovery():
    # The example compose sets exactly this, so treating it as a deliberate
    # narrowing would deny the feature to everyone who copied the file.
    cfg = config.from_env({"REQUEST_TAGS": "subs:en,subs:es,subs:auto"}).tags
    on, note = tags.discovery_enabled(cfg)
    assert on
    assert "old default" in note


def test_a_narrowed_list_is_respected():
    cfg = config.from_env({"REQUEST_TAGS": "subs:en"}).tags
    on, note = tags.discovery_enabled(cfg)
    assert not on
    assert "only those tags" in note


@pytest.mark.parametrize("value,expected", [("false", False), ("true", True)])
def test_explicit_override_wins(value, expected):
    cfg = config.from_env({"REQUEST_TAGS": "subs:en", "TAG_DISCOVERY": value}).tags
    assert tags.discovery_enabled(cfg)[0] is expected


# -- the reprocessing hazard ----------------------------------------------

def worker_with(plan, client):
    w = Worker(config.from_env({}), status.Store(), client=client)
    w.plan = plan
    w.done_id, w.failed_id = plan.done_id, plan.failed_id
    return w


def test_targets_come_from_the_plan_used_for_the_query():
    client, plan = plan_for(["subs:en", "subs:fr"])
    w = worker_with(plan, client)
    ids = {t.name: t.id for t in plan.requests.values()}
    scene = {"tags": [{"id": ids["subs:en"], "name": "subs:en"},
                      {"id": "999", "name": "4k"}]}
    assert w.targets_for(scene) == ["en"]


def test_only_the_tags_acted_on_are_stripped():
    # A request tag added while the scene was being transcribed must survive,
    # or the user's request disappears without ever being honoured.
    client, plan = plan_for(["subs:en", "subs:fr"])
    w = worker_with(plan, client)
    ids = {t.name: t.id for t in plan.requests.values()}
    scene = {"id": "5", "tags": [{"id": ids["subs:en"], "name": "subs:en"}]}
    client._scene_tags = [
        {"id": ids["subs:en"], "name": "subs:en"},
        {"id": ids["subs:fr"], "name": "subs:fr"},   # added mid-run
        {"id": "999", "name": "4k"},
    ]
    w.swap_tags(scene, ok=True)
    _, written = client.updates[0]
    assert ids["subs:en"] not in written, "the handled tag must be removed"
    assert ids["subs:fr"] in written, "the tag added mid-run must survive"
    assert "999" in written, "unrelated tags must survive"
    assert plan.done_id in written


def test_unrelated_tags_added_mid_run_are_preserved():
    client, plan = plan_for(["subs:en"])
    w = worker_with(plan, client)
    en = next(iter(plan.requests))
    scene = {"id": "5", "tags": [{"id": en, "name": "subs:en"}]}
    client._scene_tags = [{"id": en, "name": "subs:en"},
                          {"id": "42", "name": "favourite"}]
    w.swap_tags(scene, ok=False)
    _, written = client.updates[0]
    assert "42" in written
    assert plan.failed_id in written
    assert plan.done_id not in written
