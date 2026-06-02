from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import logging
import signal
import sys
import threading

from app.config import Config, ConfigError
from app.service import DigestError, estimate_digest, run_digest
from app.time_window import next_run_at


LOGGER = logging.getLogger(__name__)
STOP_EVENT = threading.Event()


def main() -> int:
    parser = argparse.ArgumentParser(description="Send daily GitHub star growth digest to WeCom.")
    parser.add_argument("--once", action="store_true", help="Run one digest immediately and exit.")
    parser.add_argument("--date", help="Target local date to report, formatted as YYYY-MM-DD.")
    parser.add_argument("--force", action="store_true", help="Send even if this target date was already sent.")
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="Only estimate GH Archive download bytes and exit.",
    )
    args = parser.parse_args()
    if args.estimate_only and not args.once:
        parser.error("--estimate-only requires --once")

    try:
        config = Config.from_env(require_notifier=not args.estimate_only)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_signal_handlers()

    try:
        target_date = _parse_date(args.date) if args.date else None
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.once:
        if args.estimate_only:
            return _estimate_once(config, target_date)
        return _run_once(config, target_date, args.force)

    if config.run_on_start:
        _run_once(config, target_date, args.force)
    return _run_scheduler(config)


def _run_once(config: Config, target_date: date | None, force: bool) -> int:
    try:
        run_digest(config, target_date=target_date, force=force)
    except (ConfigError, DigestError, RuntimeError):
        LOGGER.exception("Digest run failed")
        return 1
    return 0


def _estimate_once(config: Config, target_date: date | None) -> int:
    try:
        target_date_iso, estimated_bytes, month_used, month_budget = estimate_digest(config, target_date)
    except (ConfigError, DigestError, RuntimeError):
        LOGGER.exception("Digest estimate failed")
        return 1
    projected = month_used + estimated_bytes
    print(f"target_date={target_date_iso}")
    print(f"estimated_download={_format_bytes(estimated_bytes)}")
    print(f"daily_download_limit={_format_bytes(config.archive_max_download_bytes)}")
    print(f"monthly_projected={_format_bytes(projected)} / {_format_bytes(month_budget)}")
    print(
        "within_free_guard="
        f"{'yes' if estimated_bytes <= config.archive_max_download_bytes and projected <= month_budget else 'no'}"
    )
    return 0


def _run_scheduler(config: Config) -> int:
    LOGGER.info(
        "Scheduler started; run_time=%s timezone=%s state_file=%s",
        config.run_time,
        config.timezone_name,
        config.state_file,
    )
    while not STOP_EVENT.is_set():
        run_at = next_run_at(datetime.now(timezone.utc), config.timezone, config.run_time)
        wait_seconds = max(1.0, (run_at - datetime.now(run_at.tzinfo)).total_seconds())
        LOGGER.info("Next digest run at %s", run_at.isoformat())
        if STOP_EVENT.wait(wait_seconds):
            break
        _run_once(config, target_date=None, force=False)
    LOGGER.info("Scheduler stopped")
    return 0


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError("--date must use YYYY-MM-DD format") from exc


def _install_signal_handlers() -> None:
    def handle_signal(signum, _frame) -> None:
        LOGGER.info("Received signal %s, stopping", signum)
        STOP_EVENT.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


def _format_bytes(value: int) -> str:
    gib = 1024**3
    tib = 1024**4
    if value >= tib:
        return f"{value / tib:.2f} TiB"
    return f"{value / gib:.2f} GiB"


if __name__ == "__main__":
    raise SystemExit(main())
