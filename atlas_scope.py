"""Carry learned map data over when a client update changes the atlas scope.

The atlas scope hashes the client build, the map asset and the server address,
so a new client build opens a fresh, empty set of local_data files. For the same
server the routes, stairs, doors, notes and explored map stay valid, so the
first time a new scope appears for a server whose previous scope is known, its
files are copied under the new name with their stored scope rewritten.
"""
import json
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

LINEAGE = "atlas-lineage.json"
log = logging.getLogger(__name__)


def _read_lineage(path):
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        servers = doc.get("servers") if doc.get("version") == 1 else None
        return {str(k): str(v) for k, v in servers.items()} if isinstance(servers, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _write_lineage(path, servers):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"version": 1, "servers": servers}, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _copy_sqlite(source, target, scope):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        tables = {r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        with dst:
            if "meta" in tables:
                dst.execute("UPDATE meta SET scope=?", (scope,))
            if "store" in tables:
                dst.execute("UPDATE store SET scope=?", (scope,))


def _copy_json(source, target, scope):
    doc = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and "scope" in doc:
        doc["scope"] = scope
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _inherit_files(directory, previous, scope):
    old, new = previous[:16], scope[:16]
    sources = [p for p in directory.iterdir()
               if p.is_file() and old in p.name and p.suffix in (".sqlite3", ".json")]
    # The atlas file marks a scope as present, so it is written last.
    sources.sort(key=lambda p: p.name == f"map_atlas-{old}.sqlite3")
    copied = []
    for source in sources:
        target = directory / source.name.replace(old, new)
        if target.exists():
            continue
        partial = target.with_name(target.name + ".partial")
        try:
            (_copy_sqlite if source.suffix == ".sqlite3" else _copy_json)(source, partial, scope)
            os.replace(partial, target)
            copied.append(target.name)
        finally:
            partial.unlink(missing_ok=True)
    return copied


def inherit(directory, scope, server):
    """Make `scope` start from the previous scope's data for the same server.

    Returns the names of the files copied. Never raises: a failure only means the
    new scope starts empty, as it did before this existed.
    """
    try:
        directory = Path(directory)
        if not server or server.endswith(":0") or server.startswith(":"):
            return []
        directory.mkdir(parents=True, exist_ok=True)
        lineage_path = directory / LINEAGE
        servers = _read_lineage(lineage_path)
        previous = servers.get(server)
        if previous == scope:
            return []
        copied = []
        fresh = not (directory / f"map_atlas-{scope[:16]}.sqlite3").exists()
        if previous and fresh and (directory / f"map_atlas-{previous[:16]}.sqlite3").exists():
            copied = _inherit_files(directory, previous, scope)
            log.info("Atlas data carried over from scope %s to %s (%d files)", previous[:16], scope[:16], len(copied))
        servers[server] = scope
        _write_lineage(lineage_path, servers)
        return copied
    except Exception:
        log.warning("Could not carry atlas data over to the new scope", exc_info=True)
        return []
