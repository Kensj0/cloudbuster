"""Entry point: python -m cloudbuster_sync [--once] [--dry-run] [--config PATH]."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import __version__
from .config import load
from .engine import Engine, SyncAbort
from .immich import Immich
from .nextcloud import Nextcloud
from .state import State

log = logging.getLogger("cloudbuster.sync")
_stop = False


def _handle_stop(signum, frame):
    global _stop
    _stop = True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cloudbuster-sync", description="Two-way sync of one Immich album with one Nextcloud folder.")
    ap.add_argument("--config", default="/config/config.yaml", help="path to config.yaml")
    ap.add_argument("--once", action="store_true", help="run a single sync and exit")
    ap.add_argument("--dry-run", action="store_true", help="only log what would happen")
    ap.add_argument("--allow-mass-delete", action="store_true",
                    help="allow more deletions than sync.max_deletions_per_run (single run only)")
    args = ap.parse_args(argv)

    cfg = load(args.config)
    logging.basicConfig(level=cfg.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    # urllib3 debug logs would include URLs; keep them quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    immich = Immich(cfg.immich_url, cfg.immich_api_key)
    nc = Nextcloud(cfg.nextcloud_url, cfg.nextcloud_user, cfg.nextcloud_app_password, cfg.nextcloud_folder)
    state = State(cfg.state_path)
    engine = Engine(cfg, immich, nc, state)

    log.info("cloudbuster-sync %s starting (album=%r, interval=%ss, dry_run=%s)",
             __version__, cfg.album_name, cfg.interval_seconds, args.dry_run)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    once = args.once or args.dry_run or args.allow_mass_delete
    exit_code = 0
    while not _stop:
        try:
            res = engine.run(dry_run=args.dry_run, allow_mass_delete=args.allow_mass_delete)
            level = logging.INFO if (res.planned or res.failed) else logging.DEBUG
            log.log(level, "run done: planned=%d applied=%d failed=%d blocked_deletions=%d",
                    res.planned, res.applied, res.failed, res.deletions_blocked)
            exit_code = 1 if (res.failed or res.deletions_blocked) else 0
        except SyncAbort as exc:
            log.error("sync aborted, nothing changed: %s", exc)
            exit_code = 2
        except Exception as exc:
            log.error("sync run failed: %s: %s", type(exc).__name__, exc)
            exit_code = 1
        if once:
            break
        for _ in range(cfg.interval_seconds):
            if _stop:
                break
            time.sleep(1)

    state.close()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
