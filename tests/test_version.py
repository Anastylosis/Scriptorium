import re
import tomllib
from pathlib import Path

import scriptorium

ROOT = Path(__file__).resolve().parent.parent


def test_the_package_and_project_versions_agree():
    # The version is stamped into every generated subtitle's provenance, so
    # a stale one silently mislabels output. It drifted from 0.1.0 to 0.2.0
    # while releases went out as 0.5.0.
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert scriptorium.__version__ == declared


def test_the_version_looks_like_a_version():
    assert re.fullmatch(r"\d+\.\d+\.\d+", scriptorium.__version__)


def test_the_version_is_exposed_to_the_status_page():
    # Shown in the page footer and in /json, so it must not be importable
    # only from the package root.
    from scriptorium import status
    assert status.Store().snapshot()["version"] == scriptorium.__version__
