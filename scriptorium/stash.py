"""Stash GraphQL client. Transport and queries only, no policy."""

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

SCENE_FIELDS = "id title files { path duration } tags { id name }"
CAPTION_FIELDS = "captions { language_code caption_type }"


class StashError(RuntimeError):
    pass


class Client:
    def __init__(self, url: str, api_key: str = "", timeout: int = 60):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        # Assumed absent until probed: asking for a field this Stash does not
        # have fails the whole query, not just that field.
        self.supports_captions = False

    def execute(self, query, variables=None):
        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        req = urllib.request.Request(f"{self.url}/graphql", data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise StashError(f"Stash HTTP {e.code}: {e.read().decode()[:400]}") from None
        if body.get("errors"):
            raise StashError(f"Stash GraphQL error: {body['errors']}")
        return body["data"]

    def find_or_create_tag(self, name):
        data = self.execute(
            """query($n: String!) {
                 findTags(tag_filter: {name: {value: $n, modifier: EQUALS}},
                          filter: {per_page: -1}) { tags { id name } }
               }""",
            {"n": name},
        )
        for t in data["findTags"]["tags"]:
            if t["name"].lower() == name.lower():
                return t["id"]
        data = self.execute(
            "mutation($i: TagCreateInput!) { tagCreate(input: $i) { id name } }",
            {"i": {"name": name}},
        )
        log.info("created tag %s", name)
        return data["tagCreate"]["id"]

    def find_tags_matching(self, pattern):
        data = self.execute(
            """query($p: String!) {
                 findTags(tag_filter: {name: {value: $p, modifier: MATCHES_REGEX}},
                          filter: {per_page: -1}) { tags { id name } }
               }""",
            {"p": pattern},
        )
        return data["findTags"]["tags"]

    def all_tags(self):
        data = self.execute(
            "query { findTags(filter: {per_page: -1}) { tags { id name } } }")
        return data["findTags"]["tags"]

    def probe_captions(self):
        """Find out whether this Stash exposes Scene.captions.

        Stash's schema shifts between releases and an unknown field makes the
        entire scene query fail, so this is asked once, cheaply, rather than
        discovered when the queue is first read.
        """
        try:
            self.execute(
                "query { findScenes(filter: {per_page: 1}) { scenes { %s } } }"
                % CAPTION_FIELDS)
            self.supports_captions = True
        except StashError as e:
            self.supports_captions = False
            log.info("this Stash does not expose Scene.captions (%s); falling "
                     "back to checking the filesystem only", str(e)[:120])
        return self.supports_captions

    def find_tagged_scenes(self, tag_ids):
        # depth 0 deliberately does not descend the tag hierarchy.
        fields = SCENE_FIELDS
        if self.supports_captions:
            fields += " " + CAPTION_FIELDS
        data = self.execute(
            """query($ids: [ID!]) {
                 findScenes(
                   scene_filter: {tags: {value: $ids, modifier: INCLUDES, depth: 0}},
                   filter: {per_page: -1, sort: "id", direction: ASC}
                 ) { count scenes { %s } }
               }""" % fields,
            {"ids": list(tag_ids)},
        )
        return data["findScenes"]["scenes"]

    def scene_tags(self, scene_id):
        data = self.execute(
            "query($id: ID!) { findScene(id: $id) { tags { id name } } }",
            {"id": scene_id},
        )
        scene = data.get("findScene")
        return scene["tags"] if scene else []

    def set_scene_tags(self, scene_id, tag_ids):
        self.execute(
            "mutation($i: SceneUpdateInput!) { sceneUpdate(input: $i) { id } }",
            {"i": {"id": scene_id, "tag_ids": list(tag_ids)}},
        )

    def metadata_scan(self, paths):
        """Targeted rescan so Stash associates new caption files.

        Takes every path in one job: metadataScan is fire-and-forget, so a
        job per scene leaves Stash walking the same directory once for each
        file just written to it.
        """
        self.execute(
            "mutation($i: ScanMetadataInput!) { metadataScan(input: $i) }",
            {"i": {"paths": list(paths)}},
        )
