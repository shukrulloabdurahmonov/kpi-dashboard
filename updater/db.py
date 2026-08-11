"""Warehouse (yamato Redshift) access.

psycopg2 only — the warehouse's client_encoding=UNICODE breaks psycopg3.
One connection is opened and reused sequentially for the whole run (the
warehouse limits connections); queries are retried with backoff and the
connection is re-established if it dies mid-run.
"""

import json
import logging
import socket
import time

import psycopg2

from config import settings

log = logging.getLogger("updater.db")


def load_creds():
    with open(settings.DB_CREDS_PATH) as f:
        return json.load(f)[settings.DB_NAME]


def warehouse_reachable():
    """Fail-fast TCP probe — False almost always means the VPN is down."""
    creds = load_creds()
    try:
        sock = socket.create_connection(
            (creds["host"], int(creds["port"])), timeout=settings.VPN_PROBE_TIMEOUT
        )
        sock.close()
        return True
    except OSError as exc:
        log.error("Warehouse TCP probe failed (%s) — VPN down?", exc)
        return False


class Warehouse:
    def __init__(self):
        self._conn = None

    def connect(self):
        creds = load_creds()
        conn = psycopg2.connect(
            dbname=creds["db"],
            host=creds["host"],
            port=creds["port"],
            user=creds["user"],
            password=creds["passwd"],
            connect_timeout=settings.CONNECT_TIMEOUT,
        )
        conn.set_client_encoding("UTF8")
        self._conn = conn
        return conn

    @property
    def conn(self):
        if self._conn is None or self._conn.closed:
            self.connect()
        return self._conn

    def close(self):
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def query(self, sql, params):
        """Run a query with retries; returns (column_names, rows)."""
        last_exc = None
        for attempt in range(settings.RETRY_ATTEMPTS):
            try:
                with self.conn.cursor() as cur:
                    cur.execute(sql, params)
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                self.conn.rollback()  # end the implicit read txn cleanly
                return cols, rows
            except psycopg2.Error as exc:
                last_exc = exc
                log.warning(
                    "Query attempt %d/%d failed: %s",
                    attempt + 1, settings.RETRY_ATTEMPTS, str(exc).strip()
                )
                try:
                    self.conn.rollback()
                except psycopg2.Error:
                    self.close()  # dead connection — reconnect on next use
                if isinstance(exc, psycopg2.ProgrammingError):
                    raise  # bad SQL / missing object — retrying won't help
                if attempt < settings.RETRY_ATTEMPTS - 1:
                    delay = settings.RETRY_BACKOFF_SECONDS[
                        min(attempt, len(settings.RETRY_BACKOFF_SECONDS) - 1)
                    ]
                    log.info("Retrying in %ds", delay)
                    time.sleep(delay)
        raise last_exc
