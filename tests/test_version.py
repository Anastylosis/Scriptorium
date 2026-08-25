"""Where the version comes from.

It is stamped into every subtitle's provenance, so it outlives the container
that wrote it. It used to be a constant in two files that had to be bumped by
hand before tagging, and it drifted from 0.1.0 to 0.2.0 while releases went
out as 0.5.0 — two constants go stale together, so no test could catch it.
The git tag is now the only source, and these are the rules it goes by.
"""

import re
import tomllib
from pathlib import Path

import pytest

import scriptorium

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("raw,expected", [
    ("v0.8.0", "0.8.0"),      # what a release build sends
    ("0.8.0", "0.8.0"),       # tolerated, in case the v is ever stripped upstream
    ("v10.20.30", "10.20.30"),
])
def test_a_release_tag_becomes_the_version(raw, expected):
    assert scriptorium._resolve(raw) == expected


@pytest.mark.parametrize("raw", [
    "main",          # what the shared workflow sends off a default branch
    "master",
    "some-branch",
    "v0.8",          # a tag that is not a version
    "v0.8.0-rc1",
    "",
    None,            # no build arg at all: a checkout, or the test suite
])
def test_anything_without_a_release_tag_behind_it_claims_nothing(raw):
    # Reporting the last release number here is what mislabelled files: a dev
    # image would stamp a version it was not built from.
    assert scriptorium._resolve(raw) == "0.0.0"


def test_the_running_version_looks_like_a_version():
    # Whatever the rules produce has to be something a consumer can parse;
    # moansubs reads this field out of the provenance JSON.
    assert re.fullmatch(r"\d+\.\d+\.\d+", scriptorium.__version__)


def test_the_suite_itself_claims_no_release():
    # Nothing built the tests, so a real number here would mean the fallback
    # had been replaced by a hardcoded one again.
    assert scriptorium.__version__ == "0.0.0"


def test_pyproject_does_not_carry_a_second_version_to_drift():
    # It has to stay a placeholder. A real number here is how the old pair
    # came back into existence.
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert declared == "0.0.0"


def test_the_version_is_exposed_to_the_status_page():
    # Shown in the page footer and in /json, so it must not be importable
    # only from the package root.
    from scriptorium import status
    assert status.Store().snapshot()["version"] == scriptorium.__version__


def test_the_dockerfile_declares_the_build_arg_the_workflow_sends():
    # The workflow passing VERSION and the Dockerfile accepting it are in
    # different files; if they disagree the build still succeeds and the
    # image quietly reports 0.0.0.
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "ARG VERSION=" in dockerfile
    assert "SCRIPTORIUM_VERSION=${VERSION}" in dockerfile
    for workflow in ("release.yml", "docker-dev.yml"):
        text = (ROOT / ".github" / "workflows" / workflow).read_text()
        assert "version-build-args: true" in text, workflow
