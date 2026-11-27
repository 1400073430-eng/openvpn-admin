#!/usr/bin/env python3
"""Stage and replace managed code trees; never merge old code or touch live data."""
import argparse
import ast
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DIRECTORIES = ('backend', 'frontend', 'docs')
FILES = ('requirements.txt', 'README.md', 'CHANGELOG.md')


def validate(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target or source in target.parents or target in source.parents:
        raise ValueError('源码目录与安装目录不能相同或相互嵌套，请从独立源码目录运行修复')
    env = target / '.env'
    if env.is_file():
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.startswith('OVPM_DATA_DIR='):
                value = line.split('=', 1)[1].strip()
                if value and Path(value).resolve() != target / 'data':
                    raise ValueError('自定义数据目录需先人工迁移或核对，未覆盖旧配置和数据库')
    for name in ('backend/main.py', 'frontend/index.html', 'requirements.txt'):
        if not (source / name).is_file():
            raise ValueError('缺少部署源文件：' + name)
    for path in source.joinpath('backend').rglob('*.py'):
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    for name in DIRECTORIES + FILES:
        if (target / name).is_symlink():
            raise ValueError('拒绝覆盖符号链接：' + name)
    return source, target


def deploy(source, target, venv=None):
    source, target = validate(source, target)
    target.mkdir(parents=True, exist_ok=True)
    root = target / '.upgrade-backups'
    root.mkdir(mode=0o700, exist_ok=True)
    if root.is_symlink():
        raise ValueError('备份目录不能是符号链接')
    root.chmod(0o700)
    stage = Path(tempfile.mkdtemp(prefix='.code-stage-', dir=target))
    stage.chmod(0o700)
    backup = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-'), dir=root))
    moved, installed = [], []
    try:
        for name in DIRECTORIES:
            if (source / name).is_dir():
                shutil.copytree(source / name, stage / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
        for name in FILES:
            if (source / name).is_file():
                shutil.copy2(source / name, stage / name)
        # Normalize code readability even when the installer inherited umask 077.
        for path in stage.rglob('*'):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)
        if (target / '.env').is_file():
            shutil.copy2(target / '.env', backup / '.env')
            (backup / '.env').chmod(0o600)
        db = target / 'data' / 'ovpn_admin.db'
        if db.is_file():
            with sqlite3.connect(db.as_uri() + '?mode=ro', uri=True) as original:
                with sqlite3.connect(backup / 'ovpn_admin.db') as snapshot:
                    original.backup(snapshot)
            (backup / 'ovpn_admin.db').chmod(0o600)
        # Keep old top-level modules out of the new runtime as well.
        legacy = ('server.py', 'storage.py', 'security.py', 'routers', 'services', '__pycache__')
        for name in DIRECTORIES + FILES + legacy:
            current = target / name
            if current.exists() or current.is_symlink():
                os.replace(current, backup / name)
                moved.append(name)
            if (stage / name).exists():
                os.replace(stage / name, current)
                installed.append(name)
        if venv is not None:
            venv = Path(venv).resolve()
            if not (venv / 'bin/python').is_file():
                raise ValueError('新虚拟环境缺少 Python 入口')
            current = target / 'venv'
            if current.exists() or current.is_symlink():
                os.replace(current, backup / 'venv')
                moved.append('venv')
            os.symlink(str(venv), current)
            installed.append('venv')
        return backup
    except Exception:
        for name in reversed(installed):
            path = target / name
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        for name in reversed(moved):
            os.replace(backup / name, target / name)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--venv')
    args = parser.parse_args()
    if args.check:
        validate(args.source, args.target)
    else:
        print(deploy(args.source, args.target, args.venv))


if __name__ == '__main__':
    main()
