import logging
import os
import threading
from typing import Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

from app.tools.secrets_manager import get_secret

load_dotenv()
logger = logging.getLogger(__name__)

DB_CONFIG = {
    "dbname": get_secret("DB_NAME", os.getenv("DB_NAME", "jts_powertool_dev")),
    "user": get_secret("DB_USER", os.getenv("DB_USER", "jts_user")),
    "password": get_secret("DB_PASSWORD", os.getenv("DB_PASSWORD", "jts_dev_password")),
    "host": get_secret("DB_HOST", os.getenv("DB_HOST", "localhost")),
    "port": int(get_secret("DB_PORT", str(os.getenv("DB_PORT", "5432")))),
}

_POOL: Optional[ThreadedConnectionPool] = None
_POOL_LOCK = threading.Lock()


class PooledConnectionWrapper:
    """
    Transparent wrapper for pooled psycopg2 connections.
    Intercepts .close() to return the connection back to ThreadedConnectionPool
    instead of terminating the underlying TCP/TLS connection to the database.
    """

    def __init__(self, pool: ThreadedConnectionPool, conn):
        self._pool = pool
        self._conn = conn
        self._closed = False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)

    @property
    def closed(self) -> int:
        return 1 if self._closed else getattr(self._conn, "closed", 0)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            if self._pool is not None and self._conn is not None:
                try:
                    if not self._conn.closed and getattr(self._conn, "status", 0) != 0:
                        self._conn.rollback()
                except Exception:
                    pass
                try:
                    self._pool.putconn(self._conn)
                except Exception:
                    try:
                        self._conn.close()
                    except Exception:
                        pass
            elif self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            else:
                try:
                    self._conn.commit()
                except Exception:
                    pass
        finally:
            self.close()


def _get_pool() -> Optional[ThreadedConnectionPool]:
    global _POOL
    if _POOL is not None:
        return _POOL
    with _POOL_LOCK:
        if _POOL is None:
            try:
                minconn = int(os.getenv("DB_POOL_MIN", "2"))
                maxconn = int(os.getenv("DB_POOL_MAX", "20"))
                _POOL = ThreadedConnectionPool(
                    minconn=minconn,
                    maxconn=maxconn,
                    cursor_factory=RealDictCursor,
                    **DB_CONFIG,
                )
                logger.info(f"Initialized PostgreSQL ThreadedConnectionPool (min={minconn}, max={maxconn})")
            except Exception as e:
                logger.warning(f"Could not initialize PostgreSQL ThreadedConnectionPool: {e}")
                return None
    return _POOL


def get_db_connection():
    """
    Retrieves a thread-safe pooled database connection.
    Falls back gracefully to a direct connection if the pool is unavailable or exhausted.
    """
    pool = _get_pool()
    if pool is not None:
        try:
            conn = pool.getconn()
            if getattr(conn, "closed", 0) != 0:
                try:
                    pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = pool.getconn()
            return PooledConnectionWrapper(pool, conn)
        except Exception as e:
            logger.warning(f"Failed to borrow connection from pool ({e}); falling back to direct connection.")

    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)
