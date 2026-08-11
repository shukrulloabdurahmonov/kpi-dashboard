"""Snapshot build + integrity check + ship to the droplet.

The snapshot is a backup copy of the local store with a fresh
snapshot_info stamp. Shipping is scp to a temp name followed by an
atomic mv on the droplet, so the webapp never sees a half-copied file.
"""

import logging
import os
import sqlite3
import subprocess

from config import settings
from updater import store as store_mod
from updater.registry import METRICS

log = logging.getLogger("updater.snapshot")

MIN_METRIC_ROWS = 500  # far below a healthy snapshot (~20k), catches empty builds


def build_snapshot(store_path=None):
    """Copy the store to snapshot.sqlite.building, stamp and verify it."""
    store_path = store_path or settings.STORE_PATH
    out_path = settings.SNAPSHOT_BUILD_DIR / "snapshot.sqlite.building"
    if out_path.exists():
        out_path.unlink()

    src = sqlite3.connect(str(store_path))
    dst = sqlite3.connect(str(out_path))
    try:
        src.backup(dst)
    finally:
        src.close()

    try:
        store_mod.set_info(dst, "built_at_utc", store_mod.utcnow())
        store_mod.set_info(dst, "site_sk", settings.SITE_SK)
        store_mod.set_info(dst, "schema_version", settings.SCHEMA_VERSION)

        check = dst.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError("snapshot quick_check failed: %s" % check)

        meta_specs = {r[0] for r in dst.execute("SELECT metric FROM meta")}
        missing = {s.name for s in METRICS} - meta_specs
        if missing:
            raise RuntimeError("snapshot missing meta for specs: %s" % sorted(missing))

        n = dst.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        if n < MIN_METRIC_ROWS:
            raise RuntimeError("snapshot has only %d metric rows (< %d)" % (n, MIN_METRIC_ROWS))
        dst.commit()
    finally:
        dst.close()

    log.info("Snapshot built: %s (%d metric rows)", out_path, n)
    return out_path


def _ssh_base(env):
    key = os.path.expanduser(env.get("DROPLET_SSH_KEY", "~/.ssh/id_ed25519"))
    return ["-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]


def ship(snapshot_path):
    """scp the snapshot to <REMOTE_DIR>/data/snapshot.sqlite.tmp, then
    atomically mv it into place."""
    env = settings.load_deploy_env()
    for k in ("DROPLET_HOST", "DROPLET_USER", "REMOTE_DIR"):
        if not env.get(k):
            raise RuntimeError("config/deploy.env missing %s — cannot ship" % k)
    target = "%s@%s" % (env["DROPLET_USER"], env["DROPLET_HOST"])
    remote_tmp = "%s/data/snapshot.sqlite.tmp" % env["REMOTE_DIR"]
    remote_final = "%s/data/snapshot.sqlite" % env["REMOTE_DIR"]

    subprocess.run(
        ["scp"] + _ssh_base(env) + [str(snapshot_path), "%s:%s" % (target, remote_tmp)],
        check=True, timeout=300,
    )
    subprocess.run(
        ["ssh"] + _ssh_base(env) + [target, "mv %s %s" % (remote_tmp, remote_final)],
        check=True, timeout=60,
    )
    log.info("Snapshot shipped to %s:%s", target, remote_final)
