"""
Unified Database Adapter for STR Price Advisor.
Provides a drop-in DB-API 2.0 / sqlite3-compatible interface to Turso Cloud (LibSQL)
with transparent fallback to local sqlite3 for hermetic unit testing and offline development.
"""

import os
import time
import random
import sqlite3
import logging
import threading
from pathlib import Path

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOCAL_DB = REPO_ROOT / "data" / "reservations.db"


class LibSQLRow:
    """Row wrapper providing both dict-key and index-style access like sqlite3.Row."""
    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = list(columns)
        self._values = list(values)
        self._mapping = {k: v for k, v in zip(self._columns, self._values)}
        self._lower_mapping = {k.lower(): v for k, v in zip(self._columns, self._values)}

    def __getitem__(self, key: Union[str, int, slice]) -> Any:
        if isinstance(key, slice):
            return tuple(self._values[key])
        if isinstance(key, int):
            return self._values[key]
        if isinstance(key, str):
            if key in self._mapping:
                return self._mapping[key]
            lower = key.lower()
            if lower in self._lower_mapping:
                return self._lower_mapping[lower]
            raise KeyError(key)
        raise IndexError(f"Row index must be int, slice, or str, not {type(key).__name__}")

    def get(self, key: str, default: Any = None) -> Any:
        if key in self._mapping:
            return self._mapping[key]
        return self._lower_mapping.get(key.lower(), default)

    def keys(self) -> List[str]:
        return list(self._columns)

    def values(self) -> List[Any]:
        return list(self._values)

    def items(self) -> List[Tuple[str, Any]]:
        return list(zip(self._columns, self._values))

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


    def __repr__(self):
        return f"<LibSQLRow {self._mapping}>"


    def __eq__(self, other: Any) -> bool:
        if isinstance(other, LibSQLRow):
            return self._columns == other._columns and self._values == other._values
        if isinstance(other, (tuple, list)):
            return self._values == list(other)
        if isinstance(other, dict):
            return self._mapping == other
        return False



class LibSQLCursor:
    """Cursor wrapper conforming to Python DB-API 2.0 and sqlite3.Cursor."""
    def __init__(self, connection: "TursoRemoteConnection"):
        self._conn = connection
        self._rs = None
        self._columns: List[str] = []
        self._rows: List[LibSQLRow] = []
        self._idx: int = 0
        self.lastrowid: Optional[int] = None
        self.rowcount: int = -1

    @property
    def description(self) -> Optional[Tuple[Tuple[Any, ...], ...]]:
        """Returns DB-API 2.0 sequence of 7-item column descriptors."""
        if not self._columns:
            return None
        return tuple((col, None, None, None, None, None, None) for col in self._columns)

    def execute(self, sql: str, params: Optional[Union[Sequence[Any], Dict[str, Any]]] = None) -> "LibSQLCursor":
        self._rs = self._conn._execute_with_retry(sql, params)
        self._populate_results(self._rs)
        return self

    def executemany(self, sql: str, seq_of_params: Sequence[Union[Sequence[Any], Dict[str, Any]]]) -> "LibSQLCursor":
        self._rs, total_affected = self._conn._executemany_with_retry(sql, seq_of_params)
        self._populate_results(self._rs)
        if total_affected is not None:
            self.rowcount = total_affected
        return self


    def _populate_results(self, rs: Any):
        self._idx = 0
        if rs is not None:
            self._columns = [str(col) for col in getattr(rs, "columns", [])]
            raw_rows = getattr(rs, "rows", [])
            self._rows = [LibSQLRow(self._columns, row) for row in raw_rows]
            self.lastrowid = getattr(rs, "last_insert_rowid", None)
            self.rowcount = getattr(rs, "rows_affected", len(self._rows))
        else:
            self._columns = []
            self._rows = []
            self.lastrowid = None
            self.rowcount = 0

    def fetchone(self) -> Optional[LibSQLRow]:
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchall(self) -> List[LibSQLRow]:
        remaining = self._rows[self._idx:]
        self._idx = len(self._rows)
        return remaining

    def fetchmany(self, size: int = 1) -> List[LibSQLRow]:
        end_idx = min(self._idx + size, len(self._rows))
        batch = self._rows[self._idx:end_idx]
        self._idx = end_idx
        return batch

    def close(self):
        self._rows.clear()
        self._idx = 0

    def __iter__(self) -> "LibSQLCursor":
        return self

    def __next__(self) -> LibSQLRow:
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def __enter__(self) -> "LibSQLCursor":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


_NON_RETRYABLE_ERROR_SUBSTRINGS = (
    "no such table",
    "syntax error",
    "table already exists",
    "no such column",
    "unique constraint failed",
    "not null constraint failed",
    "datatype mismatch",
    "unauthorized",
    "forbidden",
    "401",
    "403",
    "authentication failed",
    "invalid token",
    "certificate verify failed",
    "sslcertverificationerror",
    "unable to get local issuer certificate",
)


def _is_retryable_error(e: Exception) -> bool:
    err_str = str(e).lower()
    for non_retryable in _NON_RETRYABLE_ERROR_SUBSTRINGS:
        if non_retryable in err_str:
            return False
    return True


def _patch_libsql_http_if_needed():
    """
    Workaround for libsql-client 0.3.1:
    1. Ensures valid CA root certificates (via certifi) are used by aiohttp on macOS/Linux.
    2. Catches HTTP 200 error payloads missing 'result'.
    """
    try:
        import certifi
        if "SSL_CERT_FILE" not in os.environ:
            os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:
        pass

    try:
        import ssl
        import aiohttp
        import libsql_client.http

        if not getattr(libsql_client.http.HttpClient, "_init_patched", False):
            def _safe_init(self, url: str, *, auth_token: Optional[str] = None):
                headers = {"authorization": f"Bearer {auth_token}"}
                try:
                    import certifi
                    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
                    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                except Exception:
                    connector = None
                self._session = aiohttp.ClientSession(headers=headers, connector=connector)
                self._url = url

            libsql_client.http.HttpClient.__init__ = _safe_init
            libsql_client.http.HttpClient._init_patched = True

        if not getattr(libsql_client.http.HttpClient, "_send_patched", False):
            orig_send = libsql_client.http.HttpClient._send

            async def _safe_send(self, method: str, path: str, request_body: Any) -> Any:
                data = await orig_send(self, method, path, request_body)
                if isinstance(data, dict) and "message" in data and "result" not in data:
                    raise libsql_client.LibsqlError(data["message"], data.get("code") or "UNKNOWN")
                return data

            libsql_client.http.HttpClient._send = _safe_send
            libsql_client.http.HttpClient._send_patched = True
    except Exception:
        pass

    try:
        import collections
        import asyncio
        import libsql_client.sync

        if not getattr(libsql_client.sync._AsyncExecutor, "_daemon_patched", False):
            def _daemon_executor_init(self):
                self._thread = threading.Thread(target=self._run, name="libsql_client", daemon=True)
                self._loop = asyncio.new_event_loop()
                self._lock = threading.Lock()
                self._closed = False
                self._queue = collections.deque()
                self._waker = None
                self._thread.start()

            libsql_client.sync._AsyncExecutor.__init__ = _daemon_executor_init
            libsql_client.sync._AsyncExecutor._daemon_patched = True
    except Exception:
        pass


_SHARED_TURSO_CLIENT = None
_SHARED_TURSO_KEY = None
_TURSO_CLIENT_LOCK = threading.Lock()


def _cleanup_shared_turso_client():
    global _SHARED_TURSO_CLIENT, _SHARED_TURSO_KEY
    with _TURSO_CLIENT_LOCK:
        if _SHARED_TURSO_CLIENT is not None:
            try:
                _SHARED_TURSO_CLIENT.close()
            except Exception:
                pass
            _SHARED_TURSO_CLIENT = None
            _SHARED_TURSO_KEY = None


import atexit
atexit.register(_cleanup_shared_turso_client)


def _get_shared_turso_client(url: str, auth_token: str):
    global _SHARED_TURSO_CLIENT, _SHARED_TURSO_KEY
    key = (url, auth_token)
    with _TURSO_CLIENT_LOCK:
        if _SHARED_TURSO_CLIENT is None or _SHARED_TURSO_KEY != key:
            if _SHARED_TURSO_CLIENT is not None:
                try:
                    _SHARED_TURSO_CLIENT.close()
                except Exception:
                    pass
            import libsql_client
            _patch_libsql_http_if_needed()
            _SHARED_TURSO_CLIENT = libsql_client.create_client_sync(url=url, auth_token=auth_token)
            _SHARED_TURSO_KEY = key
        return _SHARED_TURSO_CLIENT



class TursoRemoteConnection:
    """Remote LibSQL connection over HTTPS using libsql-client with retries, pooling, and cursor support."""
    def __init__(self, url: str, auth_token: str, shared: bool = True):
        _patch_libsql_http_if_needed()
        # Normalize libsql:// to https:// for reliable stateless HTTP protocol
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self._url = url
        self._auth_token = auth_token
        self._shared = shared
        if shared:
            self._client = _get_shared_turso_client(url, auth_token)
        else:
            import libsql_client
            self._client = libsql_client.create_client_sync(url=url, auth_token=auth_token)
        self.row_factory = LibSQLRow
        self._in_transaction = False

    def cursor(self) -> LibSQLCursor:
        return LibSQLCursor(self)


    def execute(self, sql: str, params: Optional[Union[Sequence[Any], Dict[str, Any]]] = None) -> LibSQLCursor:
        cur = self.cursor()
        return cur.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: Sequence[Union[Sequence[Any], Dict[str, Any]]]) -> LibSQLCursor:
        cur = self.cursor()
        return cur.executemany(sql, seq_of_params)

    def _execute_with_retry(self, sql: str, params: Optional[Union[Sequence[Any], Dict[str, Any]]] = None, max_retries: int = 3):
        param_list = list(params) if isinstance(params, (list, tuple)) else params
        for attempt in range(1, max_retries + 1):
            try:
                return self._client.execute(sql, param_list)
            except Exception as e:
                if not _is_retryable_error(e) or attempt == max_retries:
                    logger.error(f"Turso query failed (attempt {attempt}/{max_retries}): {e} | SQL: {sql[:100]}")
                    raise
                base_delay = float(os.getenv("TURSO_RETRY_DELAY", "0.5"))
                wait_time = (base_delay * (2 ** (attempt - 1))) + (random.uniform(0.1, 0.5) if base_delay > 0 else 0.0)
                logger.warning(f"Turso HTTP blip ({e}), retrying in {wait_time:.2f}s (attempt {attempt}/{max_retries})...")
                if wait_time > 0:
                    time.sleep(wait_time)

    def _executemany_with_retry(self, sql: str, seq_of_params: Sequence[Union[Sequence[Any], Dict[str, Any]]], max_retries: int = 3, chunk_size: int = 500):
        import libsql_client
        if not seq_of_params:
            return None, 0
        all_stmts = [
            libsql_client.Statement(sql, list(p) if isinstance(p, (list, tuple)) else p)
            for p in seq_of_params
        ]
        total_affected = 0
        last_rs = None

        for i in range(0, len(all_stmts), chunk_size):
            chunk = all_stmts[i : i + chunk_size]
            for attempt in range(1, max_retries + 1):
                try:
                    rs_list = self._client.batch(chunk)
                    total_affected += sum(getattr(r, "rows_affected", 0) for r in (rs_list or []))
                    if rs_list:
                        last_rs = rs_list[-1]
                    break
                except Exception as e:
                    if not _is_retryable_error(e) or attempt == max_retries:
                        logger.error(f"Turso batch failed (attempt {attempt}/{max_retries}): {e}")
                        raise
                    base_delay = float(os.getenv("TURSO_RETRY_DELAY", "0.5"))
                    wait_time = (base_delay * (2 ** (attempt - 1))) + (random.uniform(0.1, 0.5) if base_delay > 0 else 0.0)
                    if wait_time > 0:
                        time.sleep(wait_time)

        return last_rs, total_affected



    def commit(self):
        """
        No-op over stateless HTTPS protocol.
        LibSQL over HTTP (/v2/pipeline) commits per execute() and batch() request automatically.
        For multi-statement atomic operations, use executemany() or batch transactions.
        """
        pass

    def rollback(self):
        """No-op over stateless HTTPS protocol."""
        pass

    def close(self):
        if not self._shared:
            try:
                self._client.close()
            except Exception:
                pass


    def __enter__(self):
        self._in_transaction = True
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._in_transaction = False
        if exc_type is not None:
            self.rollback()
            return False
        self.commit()
        return True


_ENV_LOADED = False


def _load_env_file():
    """Ensure variables from .env are loaded into os.environ once per process."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
            _ENV_LOADED = True
        except Exception:
            pass



def is_cloud_enabled() -> bool:
    """Returns True if Turso cloud database is configured and not overridden."""
    _load_env_file()
    if os.getenv("USE_LOCAL_SQLITE", "").strip().lower() in ("1", "true", "yes"):
        return False
    url = os.getenv("TURSO_DATABASE_URL", "").strip()
    token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    return bool(url and token)


def get_db_connection(db_path: Optional[Union[Path, str]] = None):
    """
    Factory creating a database connection.
    - If db_path is an explicit test path (e.g. :memory: or temp file), returns local sqlite3.
    - If USE_LOCAL_SQLITE=1, forces local sqlite3.
    - If TURSO_DATABASE_URL is set, returns TursoRemoteConnection.
    - Fallback returns local sqlite3 at data/reservations.db.
    """
    _load_env_file()

    # Rule 1: Explicit test path override (hermetic test suite isolation)
    if db_path is not None:
        db_str = str(db_path)
        if db_str == ":memory:":
            conn = sqlite3.connect(db_str)
            conn.row_factory = sqlite3.Row
            return conn
        try:
            target = Path(db_str)
            resolved = target.resolve() if target.is_absolute() else (REPO_ROOT / target).resolve()
            if resolved != DEFAULT_LOCAL_DB.resolve():
                conn = sqlite3.connect(db_str)
                conn.row_factory = sqlite3.Row
                return conn
        except Exception:
            conn = sqlite3.connect(db_str)
            conn.row_factory = sqlite3.Row
            return conn

    # Rule 2: Explicit local SQLite override flag
    if os.getenv("USE_LOCAL_SQLITE", "").strip().lower() in ("1", "true", "yes"):
        target_path = Path(db_path) if db_path else DEFAULT_LOCAL_DB
        target_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target_path))
        conn.row_factory = sqlite3.Row
        return conn

    # Rule 3: Turso Cloud Connection
    turso_url = os.getenv("TURSO_DATABASE_URL", "").strip()
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "").strip()
    if turso_url and turso_token:
        try:
            return TursoRemoteConnection(url=turso_url, auth_token=turso_token)
        except Exception as e:
            logger.error(f"Failed to connect to Turso cloud database: {e}. Falling back to local SQLite.")

    # Rule 4: Local fallback
    target_path = Path(db_path) if db_path else DEFAULT_LOCAL_DB
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    conn.row_factory = sqlite3.Row
    return conn


# Apply compatibility and daemon thread patches at module load
_patch_libsql_http_if_needed()

