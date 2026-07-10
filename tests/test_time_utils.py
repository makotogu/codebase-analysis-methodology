from datetime import timedelta, timezone
import json
from pathlib import Path

from code_loop.config import Config
from code_loop.models import Correction
from code_loop.storage import SessionStore
from code_loop.time_utils import format_local_timestamp


LOCAL_UTC_8 = timezone(timedelta(hours=8))


def test_timestamp_formats_utc_z_and_naive_as_local_time() -> None:
    expected = "2026-07-10 22:16:47"
    assert format_local_timestamp("2026-07-10T14:16:47+00:00", target_tz=LOCAL_UTC_8) == expected
    assert format_local_timestamp("2026-07-10T14:16:47Z", target_tz=LOCAL_UTC_8) == expected
    assert format_local_timestamp("2026-07-10T14:16:47", target_tz=LOCAL_UTC_8) == expected
    assert format_local_timestamp("not-a-time", target_tz=LOCAL_UTC_8) == "not-a-time"


def test_persisted_session_correction_and_event_timestamps_remain_utc(tmp_path: Path) -> None:
    config = Config()
    store = SessionStore(tmp_path, config.repository, "utc-session")
    session = store.create("时区测试", None)
    store.append_correction(Correction(claim_id="claim_1", verdict="confirm"))
    store.event({"type": "test_event"})

    correction = json.loads(store.corrections_path.read_text(encoding="utf-8").splitlines()[0])
    event = json.loads(store.events_path.read_text(encoding="utf-8").splitlines()[0])
    assert session.created_at.endswith("+00:00")
    assert correction["timestamp"].endswith("+00:00")
    assert event["timestamp"].endswith("+00:00")
