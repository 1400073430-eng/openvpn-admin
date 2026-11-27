#!/usr/bin/env python3
"""Import the new app and validate schema on an isolated SQLite backup only."""
import argparse
import os
import sqlite3
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--data', required=True)
    args = parser.parse_args()
    source, target, data = (Path(value).resolve() for value in (args.source, args.target, args.data))
    data.mkdir(parents=True, exist_ok=True)
    os.environ['OVPM_DATA_DIR'] = str(data)
    sys.path.insert(0, str(source))
    old = target / 'data/ovpn_admin.db'
    if old.is_file():
        with sqlite3.connect(old.as_uri() + '?mode=ro', uri=True) as original:
            tables = {row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if tables and 'admins' not in tables:
                raise RuntimeError('旧数据库缺少可识别的 admins 表，需人工迁移，未修改原库')
            with sqlite3.connect(data / 'ovpn_admin.db') as snapshot:
                original.backup(snapshot)
        from backend.database import SCHEMA_SQL, init_database
        init_database()
        with sqlite3.connect(':memory:') as expected, sqlite3.connect(data / 'ovpn_admin.db') as actual:
            expected.executescript(SCHEMA_SQL)
            tables = expected.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            for (table,) in tables:
                required = {row[1] for row in expected.execute('PRAGMA table_info("' + table + '")')}
                existing = {row[1] for row in actual.execute('PRAGMA table_info("' + table + '")')}
                if not required <= existing:
                    raise RuntimeError('旧数据库结构不兼容，尚未修改原库：' + table + ' 缺少 ' + ', '.join(sorted(required - existing)))
    from backend.main import app
    if app is None:
        raise RuntimeError('应用导入失败')


if __name__ == '__main__':
    main()
