"""Bounded SQLite login throttling and one-use PNG challenges."""
import hashlib
import hmac
import io
import secrets
import time

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont

from .. import config
from ..database import get_db


def _digest(value):
    return hmac.new(config.JWT_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def _prepare(conn, now):
    conn.execute('CREATE TABLE IF NOT EXISTS login_limits (key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires INTEGER NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS login_challenges (id TEXT PRIMARY KEY, answer TEXT NOT NULL, ip TEXT NOT NULL, expires INTEGER NOT NULL)')
    conn.execute('DELETE FROM login_limits WHERE expires <= ?', (now,))
    conn.execute('DELETE FROM login_challenges WHERE expires <= ?', (now,))


def throttle(kind, ip, maximum, seconds):
    now = int(time.time())
    blocked = False
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _prepare(conn, now)
        key = kind + ':' + _digest(ip)
        row = conn.execute('SELECT count FROM login_limits WHERE key = ?', (key,)).fetchone()
        if row:
            blocked = row['count'] >= maximum
            if not blocked:
                conn.execute('UPDATE login_limits SET count = count + 1 WHERE key = ?', (key,))
        elif conn.execute('SELECT count(*) FROM login_limits').fetchone()[0] >= 10000:
            blocked = True
        else:
            conn.execute('INSERT INTO login_limits VALUES (?, 1, ?)', (key, now + seconds))
    if blocked:
        raise HTTPException(429, '请求过于频繁，请稍后再试', headers={'Retry-After': str(seconds)})


def issue(ip):
    throttle('captcha', ip, 12, 60)
    answer = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(5))
    ident = secrets.token_urlsafe(24)
    image = Image.new('RGB', (180, 60), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=30)
    for i, char in enumerate(answer):
        draw.text((10 + i * 32, 8 + secrets.randbelow(12)), char, font=font, fill=(30, 60, 90))
    for _ in range(6):
        draw.line([(secrets.randbelow(180), secrets.randbelow(60)), (secrets.randbelow(180), secrets.randbelow(60))], fill=(120, 150, 180))
    output = io.BytesIO()
    image.save(output, format='PNG')
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _prepare(conn, int(time.time()))
        if conn.execute('SELECT count(*) FROM login_challenges').fetchone()[0] >= 2000:
            raise HTTPException(429, '验证码请求过多，请稍后重试')
        conn.execute('INSERT INTO login_challenges VALUES (?, ?, ?, ?)', (ident, _digest(ident + ':' + answer), _digest(ip), int(time.time()) + 120))
    return ident, output.getvalue()


def verify(ident, answer, ip):
    valid = False
    with get_db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        _prepare(conn, int(time.time()))
        row = conn.execute('SELECT * FROM login_challenges WHERE id = ?', (ident,)).fetchone()
        if row:
            conn.execute('DELETE FROM login_challenges WHERE id = ?', (ident,))
            valid = hmac.compare_digest(row['ip'], _digest(ip)) and hmac.compare_digest(row['answer'], _digest(ident + ':' + answer.upper()))
    if not valid:
        raise HTTPException(400, '验证码错误或已过期，请刷新后重试')
