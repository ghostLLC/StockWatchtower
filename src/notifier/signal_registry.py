from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from analyzer import SignalDecision
from config_loader import BASE_DIR


REGISTRY_PATH = BASE_DIR / "data" / "signals" / "registry.json"
_lock_path = REGISTRY_PATH.parent / ".registry.lock"


class _FileLock:
    """Simple advisory file lock using O_CREAT|O_EXCL for cross-platform safety."""
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._fd: int | None = None

    def acquire(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return True
            except FileExistsError:
                if time.monotonic() > deadline:
                    # stale lock — try to break it
                    try:
                        os.remove(str(self.lock_path))
                    except OSError:
                        pass
                    if time.monotonic() > deadline + 2:
                        return False
                time.sleep(0.05)

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            try:
                os.remove(str(self.lock_path))
            except OSError:
                pass
            self._fd = None


def _ensure_registry_dir() -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load_registry() -> Any:
    _ensure_registry_dir()
    if not REGISTRY_PATH.exists():
        return {"signals": {}}
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _save_registry(registry: dict[str, Any]) -> None:
    _ensure_registry_dir()
    REGISTRY_PATH.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def _signal_key(decision: SignalDecision) -> str:
    return f"{decision.action}|{decision.symbol}"


def _prune_old_records(registry: dict[str, Any], now: datetime) -> dict[str, Any]:
    cutoff = now - timedelta(days=30)
    kept: dict[str, Any] = {}
    for key, value in registry.get("signals", {}).items():
        last_sent_at = value.get("last_sent_at")
        if not last_sent_at:
            continue
        try:
            sent_time = datetime.fromisoformat(last_sent_at)
        except ValueError:
            continue
        if sent_time >= cutoff:
            kept[key] = value
    return {"signals": kept}


def should_send_signal(decision: SignalDecision, cooldown_minutes: int) -> tuple[bool, str]:
    lock = _FileLock(_lock_path)
    lock.acquire()
    try:
        now = datetime.now()
        registry = _prune_old_records(_load_registry(), now)
        key = _signal_key(decision)
        existing = registry.get("signals", {}).get(key)
        if not existing:
            _save_registry(registry)
            return True, "首次发送"

        last_sent_at = existing.get("last_sent_at")
        if not last_sent_at:
            _save_registry(registry)
            return True, "历史记录缺失，允许补发"

        sent_time = datetime.fromisoformat(last_sent_at)
        delta = now - sent_time
        remaining_minutes = cooldown_minutes - int(delta.total_seconds() // 60)
        _save_registry(registry)
        if delta.total_seconds() < cooldown_minutes * 60:
            return False, f"距上次同类信号仅过去 {int(delta.total_seconds() // 60)} 分钟，剩余冷却约 {max(remaining_minutes, 0)} 分钟"
        return True, "已超过冷却期"
    finally:
        lock.release()


def record_sent_signal(decision: SignalDecision) -> None:
    lock = _FileLock(_lock_path)
    lock.acquire()
    try:
        now = datetime.now()
        registry = _prune_old_records(_load_registry(), now)
        key = _signal_key(decision)
        registry.setdefault("signals", {})[key] = {
            "last_sent_at": now.isoformat(timespec="seconds"),
            "summary": decision.summary,
            "urgency": decision.urgency,
        }
        _save_registry(registry)
    finally:
        lock.release()
