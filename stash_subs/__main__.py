import logging
import sys

from . import config, logsetup, status
from .worker import Control, Worker

log = logging.getLogger(__name__)


def main(argv=None):
    ring = logsetup.configure()
    try:
        cfg = config.from_env()
    except config.ConfigError as e:
        logging.error("configuration error: %s", e)
        return 2

    store = status.Store()
    control = Control()

    # Started before the tag setup and any model pull so the page is
    # reachable while those are still running.
    status.serve(store, ring, control, cfg.server.host, cfg.server.port)
    log.info("status page on http://%s:%d", cfg.server.host, cfg.server.port)
    log.info("stash-subs starting — Stash at %s", cfg.stash.url)

    worker = Worker(cfg, store, control)
    try:
        worker.bootstrap()
    except Exception as e:
        log.error("startup failed: %s", e)
        return 1
    store.update(status="idle", scene=None, stage=None)
    return worker.run()


if __name__ == "__main__":
    sys.exit(main())
