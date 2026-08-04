"""Discovering which `subs:<lang>` tags the library is asking for.

The set is re-read every poll rather than resolved once at startup, so a tag
created after the worker booted is picked up without a restart.
"""

import logging
from dataclasses import dataclass, field

from . import langs

log = logging.getLogger(__name__)

PREFIX = "subs:"
AUTO = "auto"

# The list the project shipped in its example compose. A user who still has
# it set almost certainly copied it rather than chose it, so it should not
# be read as an instruction to disable discovery.
HISTORIC_DEFAULT = frozenset({"subs:en", "subs:es", "subs:auto"})


@dataclass(frozen=True)
class RequestTag:
    id: str
    name: str
    lang: str        # an ISO 639 code, or "auto"


@dataclass(frozen=True)
class Plan:
    requests: dict = field(default_factory=dict)     # tag id -> RequestTag
    rejected: dict = field(default_factory=dict)     # tag name -> reason
    done_id: str = ""
    failed_id: str = ""

    @property
    def ids(self):
        return list(self.requests)

    def names(self):
        return sorted(t.name for t in self.requests.values())


def discovery_enabled(cfg) -> tuple[bool, str | None]:
    """(enabled, one-time note to log at startup)."""
    if cfg.discover == "false":
        return False, None
    if cfg.discover == "true":
        return True, None
    if not cfg.request_explicit:
        return True, None
    if frozenset(cfg.request) == HISTORIC_DEFAULT:
        return True, (
            "REQUEST_TAGS is set to the old default, so tag discovery stays on "
            "and any subs:<lang> tag will be picked up. Remove REQUEST_TAGS to "
            "silence this, or set TAG_DISCOVERY=false to pin the list.")
    # A narrowed list was a deliberate choice; respect it.
    return False, (
        f"REQUEST_TAGS is set to {', '.join(cfg.request)}, so only those tags "
        f"are processed. Remove it to accept any subs:<lang> tag.")


def classify(name):
    """(lang, reason). Exactly one is None."""
    if not name.lower().startswith(PREFIX):
        return None, None
    suffix = name[len(PREFIX):].strip()
    if suffix.lower() == AUTO:
        return AUTO, None
    if not suffix:
        return None, "empty language code."
    code = langs.normalize(suffix)
    if code is None:
        return None, langs.reject_reason(suffix)
    return code, None


def bootstrap(client, cfg):
    """Create the tags the worker relies on. Only subs:en is made up front —
    other languages are created by the user, on demand."""
    created = list(cfg.create) if cfg.create else []
    for name in created:
        client.find_or_create_tag(name)
    return (client.find_or_create_tag(cfg.done),
            client.find_or_create_tag(cfg.failed))


def _all_subs_tags(client):
    """Every tag starting with `subs:`.

    Tries a server-side regex first and falls back to listing tags, because
    older Stash builds reject MATCHES_REGEX. The result is filtered locally
    either way rather than trusting the server's casing semantics.
    """
    try:
        tags = client.find_tags_matching("(?i)^subs:")
    except Exception:
        tags = client.all_tags()
    return [t for t in tags if t["name"].lower().startswith(PREFIX)]


def discover(client, cfg, done_id, failed_id, previous=None):
    reserved = {cfg.done.lower(), cfg.failed.lower()}
    reserved.update(n.lower() for n in (cfg.ignore or []))

    requests, rejected = {}, {}
    for tag in _all_subs_tags(client):
        name = tag["name"]
        if name.lower() in reserved:
            continue
        lang, reason = classify(name)
        if reason is not None:
            rejected[name] = reason
            continue
        if lang is None:
            continue
        requests[tag["id"]] = RequestTag(id=tag["id"], name=name, lang=lang)

    plan = Plan(requests=requests, rejected=rejected,
                done_id=done_id, failed_id=failed_id)
    _log_changes(plan, previous)
    return plan


def fixed(client, cfg, done_id, failed_id):
    """The pre-discovery behaviour: exactly the configured tag names."""
    requests = {}
    for name in cfg.request:
        lang, reason = classify(name)
        if lang is None:
            log.warning("ignoring request tag %s: %s", name, reason or "not a subs: tag")
            continue
        requests[client.find_or_create_tag(name)] = RequestTag(
            id="", name=name, lang=lang)
    requests = {tid: RequestTag(id=tid, name=t.name, lang=t.lang)
                for tid, t in requests.items()}
    return Plan(requests=requests, done_id=done_id, failed_id=failed_id)


def _log_changes(plan, previous):
    """Log only what changed — a stray subs:pt-BR must not warn every poll."""
    before_ok = set(previous.requests) if previous else set()
    before_bad = set(previous.rejected) if previous else set()
    for tid, tag in plan.requests.items():
        if tid not in before_ok:
            label = "any spoken language" if tag.lang == AUTO else langs.name_of(tag.lang)
            log.info("request tag %s (%s)", tag.name, label)
    for name, reason in plan.rejected.items():
        if name not in before_bad:
            log.warning("tag %s ignored: %s", name, reason)
