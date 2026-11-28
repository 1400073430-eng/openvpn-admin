"""Real SQLite/Cookie/API regression; privileged operations are mocked."""
from contextlib import ExitStack
from pathlib import Path
import secrets
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend import config, database, selftest, security
from backend.routers import auth, dns, settings
from backend.services import blacklist_service
from backend.services.cmd_runner import CommandResult


class PlatformRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(security, "_LOGIN_FAILS", {}))
        root = Path(self.temp.name)
        for key, val in {
            'DATA_DIR': root, 'DATABASE_PATH': root / 'test.db',
            'BLACKLIST_DOMAIN_FILE': root / 'domains.txt',
            'BLACKLIST_STATE_FILE': root / 'state.json',
            'JWT_SECRET': secrets.token_hex(32),
            'DEFAULT_ADMIN_PASSWORD': 'Test-only-password-123',
            'DEFAULT_ADMIN_USER': 'admin',
        }.items():
            self.stack.enter_context(patch.object(config, key, val))
        database.init_database()
        app = FastAPI()
        for router in (auth.router, dns.router, settings.router):
            app.include_router(router)
        self.client = TestClient(app)
        original_post = self.client.post
        def post_with_captcha(url, **kwargs):
            if url == '/api/auth/login':
                from backend.services import login_guard
                with patch.object(login_guard.secrets, 'choice', return_value='A'):
                    challenge = self.client.get('/api/auth/captcha').json()
                kwargs['json'] = dict(kwargs['json'], captcha_id=challenge['id'], captcha_answer='AAAAA')
            return original_post(url, **kwargs)
        self.client.post = post_with_captcha
        self.addCleanup(self.client.close)

    def login(self, password='Test-only-password-123'):
        response = self.client.post('/api/auth/login', json={'username': 'admin', 'password': password})
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def test_login_cookie_flags_and_me(self):
        response = self.login()
        cookie = response.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=strict', cookie)
        self.assertNotIn('access_token', response.json())
        self.assertEqual(self.client.get('/api/auth/me').json()['data']['username'], 'admin')

    def test_wrong_password_and_lockout(self):
        for _ in range(5):
            self.assertEqual(self.client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong-password'}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong-password'}).status_code, 429)

    def test_logout_revokes_copied_cookie(self):
        self.login()
        token = self.client.cookies.get(config.AUTH_COOKIE_NAME)
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        self.client.cookies.set(config.AUTH_COOKIE_NAME, token)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)

    def test_password_change_revokes_all_sessions(self):
        self.login()
        old = self.client.cookies.get(config.AUTH_COOKIE_NAME)
        self.login()
        response = self.client.post('/api/auth/change-password', json={'old_password': 'Test-only-password-123', 'new_password': 'Changed-test-password-456'})
        self.assertEqual(response.status_code, 200)
        self.client.cookies.set(config.AUTH_COOKIE_NAME, old)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.client.cookies.clear()
        self.login('Changed-test-password-456')

    def test_username_change_requires_password_and_revokes_old_sessions(self):
        self.login()
        old_token = self.client.cookies.get(config.AUTH_COOKIE_NAME)
        self.assertEqual(self.client.post('/api/auth/change-username', json={
            'new_username': 'new_admin', 'current_password': 'incorrect-password'
        }).status_code, 400)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 200)
        self.assertEqual(self.client.post('/api/auth/change-username', json={
            'new_username': 'bad-name', 'current_password': 'Test-only-password-123'
        }).status_code, 422)
        response = self.client.post('/api/auth/change-username', json={
            'new_username': 'new_admin', 'current_password': 'Test-only-password-123'
        })
        self.assertEqual(response.status_code, 200, response.text)
        database.init_database()  # 模拟服务重启，不能重新创建默认管理员
        with database.get_db() as conn:
            admins = [row['username'] for row in conn.execute('SELECT username FROM admins')]
        self.assertEqual(admins, ['new_admin'])
        self.client.cookies.set(config.AUTH_COOKIE_NAME, old_token)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.client.post('/api/auth/login', json={
            'username': 'admin', 'password': 'Test-only-password-123'
        }).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={
            'username': 'new_admin', 'password': 'Test-only-password-123'
        }).status_code, 200)

    def test_login_rejects_credentials_changed_before_session_creation(self):
        original = auth.create_access_token
        for operation in ('password', 'username'):
            with self.subTest(operation=operation):
                with database.get_db() as conn:
                    conn.execute("UPDATE admins SET username = 'admin', password_hash = ?", (security.hash_password('Test-only-password-123'),))
                def change_before_insert(username):
                    with database.get_db() as conn:
                        if operation == 'password':
                            conn.execute("UPDATE admins SET password_hash = ? WHERE username = ?", (security.hash_password('Changed-password-456'), username))
                        else:
                            conn.execute("UPDATE admins SET username = 'renamed' WHERE username = ?", (username,))
                        conn.execute("UPDATE auth_sessions SET revoked_at = datetime('now') WHERE username = ?", (username,))
                    return original(username)
                with patch.object(auth, 'create_access_token', side_effect=change_before_insert):
                    response = self.client.post('/api/auth/login', json={'username': 'admin', 'password': 'Test-only-password-123'})
                self.assertEqual(response.status_code, 401, response.text)
                with database.get_db() as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0], 0)

    def test_database_error_handler_returns_controlled_response(self):
        import asyncio
        from backend.main import db_error_handler
        from starlette.requests import Request
        response = asyncio.run(db_error_handler(Request({'type': 'http'}), database.DatabaseError('internal detail')))
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(b'internal detail', response.body)

    def test_audit_and_session_retention(self):
        from backend.services import audit_service
        with database.get_db() as conn:
            conn.execute("INSERT INTO auth_sessions (jti, username, expires_at) VALUES ('expired', 'admin', datetime('now', '-1 day'))")
            conn.execute("INSERT INTO audit_logs (username, action, created_at) VALUES ('admin', 'old', datetime('now', '-31 days'))")
        with patch.object(audit_service, '_last_database', None):
            audit_service.cleanup_records()
        with database.get_db() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM auth_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM audit_logs WHERE action = 'old'").fetchone()[0], 0)

    def test_audit_control_characters_cannot_forge_log_lines(self):
        from backend.services.audit_service import log_action
        log_action('admin', 'test', 'detail\nFORGED\rline\x00')
        with database.get_db() as conn:
            detail = conn.execute("SELECT detail FROM audit_logs WHERE action = 'test'").fetchone()[0]
        self.assertNotIn('\n', detail)
        self.assertNotIn('\r', detail)
        self.assertNotIn('\x00', detail)

    def test_cleanup_expires_only_stale_challenges_limits_and_sessions(self):
        import time
        from backend.services import audit_service, login_guard
        now = int(time.time())
        with database.get_db() as conn:
            login_guard._prepare(conn, now)
            for kind, expiry in (('old', now - 1), ('valid', now + 600)):
                conn.execute('INSERT INTO login_limits VALUES (?, 1, ?)', (kind, expiry))
                conn.execute('INSERT INTO login_challenges VALUES (?, ?, ?, ?)', (kind, 'digest', 'ip', expiry))
            conn.execute("INSERT INTO auth_sessions (jti, username, expires_at) VALUES ('active', 'admin', datetime('now', '+1 hour'))")
            conn.execute("INSERT INTO auth_sessions (jti, username, expires_at) VALUES ('expired', 'admin', datetime('now', '-1 hour'))")
        counts = audit_service.cleanup_records(force=True)
        self.assertEqual(counts['login_limits'], 1)
        self.assertEqual(counts['login_challenges'], 1)
        with database.get_db() as conn:
            self.assertEqual([r[0] for r in conn.execute('SELECT id FROM login_challenges')], ['valid'])
            self.assertEqual([r[0] for r in conn.execute('SELECT key FROM login_limits')], ['valid'])
            self.assertEqual([r[0] for r in conn.execute('SELECT jti FROM auth_sessions')], ['active'])
            self.assertEqual(conn.execute('SELECT count(*) FROM admins').fetchone()[0], 1)

    def test_settings_write_and_read(self):
        self.login()
        response = self.client.put('/api/settings', json={'server_ip': '192.0.2.10', 'vpn_port': 1195})
        self.assertEqual(response.status_code, 200)
        with patch.object(settings, '_get_service_status', return_value={}):
            data = self.client.get('/api/settings').json()['data']
        self.assertEqual(data['network']['server_ip'], '192.0.2.10')
        self.assertEqual(data['network']['vpn_port'], '1195')

    def test_https_enabled_matches_note(self):
        self.login()
        with patch.object(config, 'HTTPS_ENABLED', True), patch.object(config, 'safe_exists', return_value=True), patch.object(settings, '_get_service_status', return_value={}):
            value = self.client.get('/api/settings').json()['data']['https']
        self.assertTrue(value['enabled'])
        self.assertNotIn('当前使用 HTTP', value['note'])

    def test_blacklist_add_duplicate_toggle_delete(self):
        self.login()
        with patch.object(dns, '_sync', return_value={'domains': 1}):
            response = self.client.post('/api/dns', json={'domain': 'example.com', 'remark': 'test'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.post('/api/dns', json={'domain': 'example.com'}).status_code, 400)
            rows = self.client.get('/api/dns').json()['data']['list']
            self.assertEqual(len(rows), 1)
            ident = rows[0]['id']
            self.assertEqual(self.client.put(f'/api/dns/{ident}', json={'enabled': False}).status_code, 200)
            self.assertEqual(self.client.delete(f'/api/dns/{ident}').status_code, 200)
            self.assertEqual(self.client.get('/api/dns').json()['data']['total'], 0)

    def test_blacklist_failed_add_can_retry(self):
        self.login()
        with patch.object(dns, '_sync', side_effect=[blacklist_service.BlacklistError('simulated failure'), {}]):
            self.assertEqual(self.client.post('/api/dns', json={'domain': 'example.com'}).status_code, 500)
        self.assertEqual(self.client.get('/api/dns').json()['data']['total'], 0)
        with patch.object(dns, '_sync', return_value={}):
            self.assertEqual(self.client.post('/api/dns', json={'domain': 'example.com'}).status_code, 200)

    def test_toggle_failure_preserves_original_enabled(self):
        self.login()
        with patch.object(dns, '_sync', return_value={}):
            self.client.post('/api/dns', json={'domain': 'example.com'})
        ident = self.client.get('/api/dns').json()['data']['list'][0]['id']
        with patch.object(dns, '_sync', side_effect=[blacklist_service.BlacklistError('fail'), {}]):
            self.assertEqual(self.client.put(f'/api/dns/{ident}', json={'enabled': True}).status_code, 500)
        self.assertEqual(self.client.get('/api/dns').json()['data']['list'][0]['enabled'], 1)

    def test_delete_failure_preserves_all_row_fields(self):
        self.login()
        with patch.object(dns, '_sync', return_value={}):
            self.client.post('/api/dns', json={'domain': 'example.com', 'remark': 'keep this note'})
        before = self.client.get('/api/dns').json()['data']['list'][0]
        with patch.object(dns, '_sync', side_effect=[blacklist_service.BlacklistError('fail'), {}]):
            self.assertEqual(self.client.delete('/api/dns/' + str(before['id'])).status_code, 500)
        self.assertEqual(before, self.client.get('/api/dns').json()['data']['list'][0])

    def test_batch_limit_returns_400(self):
        self.login()
        domains = '\n'.join('test%d.example.com' % n for n in range(201))
        response = self.client.post('/api/dns/batch', json={'domains': domains})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.client.get('/api/dns').json()['data']['total'], 0)

    def test_blacklist_sync_failure_restores_desired_file(self):
        config.BLACKLIST_DOMAIN_FILE.write_text('old.example.com\n')
        with patch.object(blacklist_service, 'run_as_root', return_value=CommandResult(False, '', 'simulated', 1)):
            with self.assertRaises(blacklist_service.BlacklistError):
                blacklist_service.sync(['new.example.com'])
        self.assertEqual(config.BLACKLIST_DOMAIN_FILE.read_text(), 'old.example.com\n')

    def test_four_selftests_use_real_auth_and_no_network(self):
        with patch('socket.getaddrinfo', side_effect=AssertionError('unexpected external DNS')):
            summary = selftest.run_all_tests()
        self.assertTrue(summary['all_passed'], summary)
        self.assertEqual(summary['total'], 4)

class MainRouteSmoke(unittest.TestCase):
    def test_all_existing_business_routes_remain_protected(self):
        from backend.main import app
        from backend.services import vpn_access
        import ipaddress
        self.addCleanup(patch.stopall)
        patch.object(vpn_access, 'NETWORKS', (ipaddress.ip_network('10.8.0.0/24'),)).start()
        client = TestClient(app, client=('10.8.0.2', 12345))  # No context manager: don't start the real tracker.
        self.addCleanup(client.close)
        checked = 0
        import json
        paths = app.openapi()['paths']
        actual = sorted(method.upper() + ' ' + path for path, definition in paths.items()
                        for method in definition if method.upper() in ('GET', 'POST', 'PUT', 'DELETE') and path.startswith('/api/'))
        expected = json.loads((Path(__file__).parent / 'api_routes.json').read_text())
        self.assertEqual(actual, expected)
        for entry in actual:
            method, path = entry.split(' ', 1)
            if path in ('/api/health', '/api/auth/login', '/api/auth/captcha'):
                continue
            path = path.replace('{name}', 'test-client').replace('{client_id}', '1').replace('{domain_id}', '1')
            response = client.request(method, path, json={} if method in ('POST', 'PUT') else None)
            self.assertEqual(response.status_code, 401, (method, path, response.text))
            checked += 1
        self.assertGreaterEqual(checked, 20)
        self.assertEqual(client.get('/api/health').status_code, 200)
        self.assertEqual(client.get('/').status_code, 200)
        for page in ('dashboard', 'clients', 'connections', 'dns', 'audit', 'settings'):
            self.assertEqual(client.get('/pages/' + page + '.html').status_code, 200)
        self.assertEqual(client.get('/pages/unknown.html').status_code, 404)
        self.assertEqual(app.version, '5.1.0')


if __name__ == '__main__':
    unittest.main()
