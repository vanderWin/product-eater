"""Session data helpers: Parquet file storage per session."""
import json
import os
import time
from pathlib import Path

import pandas as pd

SESSION_STORE = Path("session_store")
MAX_AGE_SECONDS = 4 * 60 * 60  # 4 hours


def _session_dir(session_id: str) -> Path:
    d = SESSION_STORE / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_parquet(session_id: str, name: str, df: pd.DataFrame) -> None:
    path = _session_dir(session_id) / f"{name}.parquet"
    df.to_parquet(path, index=True)


def load_parquet(session_id: str, name: str) -> pd.DataFrame | None:
    path = _session_dir(session_id) / f"{name}.parquet"
    if not path.exists():
        return None
    return pd.read_parquet(path)


def save_json(session_id: str, name: str, data: object) -> None:
    path = _session_dir(session_id) / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_json(session_id: str, name: str) -> object:
    path = _session_dir(session_id) / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def clear_session_files(session_id: str) -> None:
    d = _session_dir(session_id)
    for f in d.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass


def cleanup_old_sessions() -> None:
    """Delete session directories older than MAX_AGE_SECONDS."""
    now = time.time()
    if not SESSION_STORE.exists():
        return
    for d in SESSION_STORE.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            mtime = max(f.stat().st_mtime for f in d.iterdir() if f.is_file())
        except Exception:
            mtime = d.stat().st_mtime
        if now - mtime > MAX_AGE_SECONDS:
            for f in d.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
            try:
                d.rmdir()
            except Exception:
                pass
