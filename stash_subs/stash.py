"""Stash GraphQL client. Transport and queries only, no policy."""

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

SCENE_FIELDS = "id title files { path duration } tags { id name }"


class StashError(RuntimeError):
    pass


class Client:
    def __init__(self, url: str, api_key: str = "", timeout: int = 60):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

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

    def find_tagged_scenes(self, tag_ids):
        # depth 0 deliberately does not descend the tag hierarchy.
        data = self.execute(
            """query($ids: [ID!]) {
                 findScenes(
                   scene_filter: {tags: {value: $ids, modifier: INCLUDES, depth: 0}},
                   filter: {per_page: -1, sort: "id", direction: ASC}
                 ) { count scenes { %s } }
               }""" % SCENE_FIELDS,
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

    def metadata_scan(self, path):
        """Targeted rescan so Stash associates a new caption file."""
        self.execute(
            "mutation($i: ScanMetadataInput!) { metadataScan(input: $i) }",
            {"i": {"paths": [path]}},
        )
