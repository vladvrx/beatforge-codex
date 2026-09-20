"""Offline recovery of interrupted uploads; never removes completed source audio."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from beatforge.connector_store import ConnectorStore


def recover_uploads(database: Path, *, apply: bool = False) -> dict:
    """The operator must stop the gateway before applying this operation.

    SQLite locks cannot stop a process already streaming to a filesystem handle.
    Recovery therefore deliberately has no REST/MCP or automatic startup adapter.
    """
    database = Path(database).resolve(strict=True)
    store = ConnectorStore(database)
    root = database.parent / 'uploads'
    if root.is_symlink() or (root.exists() and root.resolve() != root):
        raise ValueError('Upload directory must not redirect outside the database directory')
    with store.connect() as db:
        db.execute('BEGIN EXCLUSIVE')
        rows = db.execute("SELECT id FROM uploads WHERE state='writing' ORDER BY id").fetchall()
        paths = []
        for row in rows:
            identifier = row['id']
            if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
                raise ValueError('Invalid upload identifier; recovery stopped')
            for suffix in ('.part', '.audio'):
                candidate = root / (identifier + suffix)
                if candidate.is_symlink() or candidate.resolve().parent != root:
                    raise ValueError('Upload path redirects outside its storage directory')
                if candidate.exists() and not candidate.is_file():
                    raise ValueError('Upload path is not a regular file')
                paths.append(candidate)
        report = {'mode':'apply' if apply else 'dry-run', 'interruptedUploads':len(rows),
                  'partialFiles':sum(p.exists() for p in paths), 'completedUploadsTouched':0}
        if apply:
            # Validate the complete set before changing any files. If interrupted
            # here, metadata stays writing and rerunning recovery is safe.
            for candidate in paths:
                candidate.unlink(missing_ok=True)
            db.execute("UPDATE uploads SET state='pending',sha256=NULL WHERE state='writing'")
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--gateway-stopped', action='store_true',
                        help='Confirm all gateway processes using this database have been stopped')
    args = parser.parse_args()
    if args.apply and not args.gateway_stopped:
        parser.error('--apply requires --gateway-stopped; stop the service first')
    print(json.dumps(recover_uploads(args.database, apply=args.apply), indent=2))


if __name__ == '__main__':
    main()
