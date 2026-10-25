"""Consistent online SQLite snapshots plus a checksummed download manifest."""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

SNAPSHOT_NAME = re.compile(r'tools-\d{8}T\d{6}Z-[a-f0-9]{12}\.sqlite3\Z')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('SQLite integrity check failed')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'members', 'tools', 'movements'} <= tables:
            raise ValueError('This is not a tools inventory database')


def create_snapshot(database, destination, keep=30):
    if keep < 1:
        raise ValueError('Keep must be at least 1')
    source = Path(database).resolve(strict=True)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    filename = f'tools-{stamp}-{uuid.uuid4().hex[:12]}.sqlite3'
    final = destination / filename
    temporary = final.with_suffix('.tmp')
    try:
        # backup() includes committed WAL data; copying the live DB file does not.
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(temporary)) as dst:
                os.chmod(temporary, 0o600)
                src.backup(dst, pages=256)
        verify(temporary)
        os.replace(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)

    snapshots = sorted((path for path in destination.iterdir() if SNAPSHOT_NAME.fullmatch(path.name)),
                       key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
    kept = snapshots[:keep]
    manifest = {'snapshots': [{'filename': path.name, 'sha256': sha256(path)} for path in kept]}
    manifest_temp = destination / f'manifest-{uuid.uuid4().hex}.tmp'
    try:
        manifest_temp.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        os.chmod(manifest_temp, 0o600)
        os.replace(manifest_temp, destination / 'manifest.json')
    finally:
        manifest_temp.unlink(missing_ok=True)
    # Only rotate our own snapshots, and only after publishing a complete manifest.
    for old in snapshots[keep:]:
        old.unlink()
    return final


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--keep', type=int, default=30)
    args = parser.parse_args()
    print(create_snapshot(args.database, args.destination, args.keep))
