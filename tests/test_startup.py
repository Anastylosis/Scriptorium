"""What the worker says on the way up.

A warning here is worth more than one at the point of failure: the model is
loaded lazily, so anything wrong with the way it is configured surfaces in
the middle of the first scene rather than at startup.
"""

import logging

from scriptorium import config, status
from scriptorium.folder import FolderLibrary
from scriptorium.worker import Worker


def boot(tmp_path, caplog, **env):
    cfg = config.from_env({"WATCH_DIRS": str(tmp_path),
                           "STATE_DIR": str(tmp_path / ".state"), **env})
    w = Worker(cfg, status.Store(), library=FolderLibrary(cfg))
    with caplog.at_level(logging.INFO):
        w.bootstrap()
    return caplog.text


def test_a_device_the_image_cannot_serve_is_named_at_startup(tmp_path, caplog):
    text = boot(tmp_path, caplog, DEVICE="cuda")
    assert "DEVICE=cuda" in text
    assert "cuDNN" in text


def test_the_supported_device_says_nothing(tmp_path, caplog):
    assert "cuDNN" not in boot(tmp_path, caplog)
