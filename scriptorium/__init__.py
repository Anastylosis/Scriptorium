"""Version resolution.

The version is stamped into every generated subtitle's provenance and shown
on the status page, so a wrong one mislabels files that outlive the container
by years. It used to live here and in pyproject.toml, and had to be bumped in
both by hand before tagging; it drifted twice anyway, because two constants
in two files go stale together and no test can tell.

There is now one source: the git tag. The release build passes it in as
SCRIPTORIUM_VERSION (see Dockerfile and .github/workflows/release.yml).
Anything without a tag behind it — a dev image, a checkout, the test suite —
must not claim a release number, so it reports 0.0.0 and lets the image tag
say which build it came from.
"""

import os
import re


def _resolve(raw):
    """A release version from the tag, or 0.0.0 for everything else.

    The shared publish workflow sends the tag as-is (`v0.8.0`) on a release
    and the branch name (`main`) otherwise, so the shape is what decides,
    not the presence of the variable.
    """
    raw = (raw or "").removeprefix("v")
    return raw if re.fullmatch(r"\d+\.\d+\.\d+", raw) else "0.0.0"


__version__ = _resolve(os.environ.get("SCRIPTORIUM_VERSION"))
