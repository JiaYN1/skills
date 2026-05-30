from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from app.bilibili import BilibiliClient, BilibiliError, DynamicItem
from app.config import Config, ConfigError
from app.notifiers import NotifyError, create_notifier
from app.state import StateStore


LOGGER = logging.getLogger(__name__)
STOP_EVENT = threading.Event()


def main() -> int:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _install_signal_handlers()

    client = BilibiliClient(
        timeout=config.request_timeout,
        cookie=config.bili_cookie,
        api_url=config.bili_api_url,
    )
    notifier = create_notifier(config)
    state = StateStore(config.state_file, max_seen_per_uid=config.max_seen_per_uid)
    state.load()

    LOGGER.info(
        "Watching %s Bilibili uid(s), poll interval=%ss, state=%s",
        len(config.uids),
        config.poll_interval,
        config.state_file,
    )

    while not STOP_EVENT.is_set():
        started_at = time.monotonic()
        for uid in config.uids:
            if STOP_EVENT.is_set():
                break
            _poll_uid(uid, config, client, notifier, state)

        elapsed = time.monotonic() - started_at
        wait_seconds = max(1.0, config.poll_interval - elapsed)
        STOP_EVENT.wait(wait_seconds)

    LOGGER.info("Stopped")
    return 0


def _poll_uid(uid: str, config: Config, client: BilibiliClient, notifier, state: StateStore) -> None:
    try:
        items = client.fetch_latest(uid)
    except BilibiliError:
        LOGGER.exception("Failed to fetch dynamics for uid=%s", uid)
        return

    if not items:
        LOGGER.debug("No dynamics returned for uid=%s", uid)
        return

    if not state.has_uid(uid) and not config.notify_existing_on_first_run:
        state.mark_seen(uid, [item.dynamic_id for item in items])
        LOGGER.info("Initialized uid=%s with %s existing dynamic(s), no notification sent", uid, len(items))
        return

    seen = state.seen_set(uid)
    new_items = [item for item in items if item.dynamic_id not in seen]
    if not new_items:
        LOGGER.debug("No new dynamics for uid=%s", uid)
        return

    sent_ids: list[str] = []
    for item in reversed(new_items):
        if STOP_EVENT.is_set():
            break
        if _send_item(item, notifier):
            sent_ids.append(item.dynamic_id)
        else:
            break

    if sent_ids:
        state.mark_seen(uid, sent_ids)


def _send_item(item: DynamicItem, notifier) -> bool:
    try:
        notifier.send(item)
    except NotifyError:
        LOGGER.exception("Failed to notify dynamic_id=%s uid=%s", item.dynamic_id, item.uid)
        return False

    LOGGER.info("Notified dynamic_id=%s author=%s title=%s", item.dynamic_id, item.author_name, item.title)
    return True


def _install_signal_handlers() -> None:
    def handle_signal(signum, _frame) -> None:
        LOGGER.info("Received signal %s, stopping", signum)
        STOP_EVENT.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)


if __name__ == "__main__":
    raise SystemExit(main())

