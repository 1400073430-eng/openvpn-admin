import asyncio
import unittest
from backend.services.request_limits import RequestBodyLimitMiddleware


class RequestLimitTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, chunks, headers=(), timeout=1, delay=0):
        called, responses = [], []
        async def app(scope, receive, send):
            called.append((await receive())['body'])
        iterator = iter(chunks)
        async def receive():
            if delay:
                await asyncio.sleep(delay)
            return next(iterator)
        async def send(message):
            responses.append(message)
        middleware = RequestBodyLimitMiddleware(app, max_bytes=8, timeout=timeout)
        await middleware({'type': 'http', 'method': 'POST', 'headers': headers}, receive, send)
        status = next((r['status'] for r in responses if r['type'] == 'http.response.start'), None)
        return called, status

    async def test_chunked_overflow_never_reaches_handler(self):
        called, status = await self.run_request([{'type': 'http.request', 'body': b'12345', 'more_body': True}, {'type': 'http.request', 'body': b'6789'}])
        self.assertFalse(called)
        self.assertEqual(status, 413)

    async def test_body_replayed_intact(self):
        called, status = await self.run_request([{'type': 'http.request', 'body': b'123', 'more_body': True}, {'type': 'http.request', 'body': b'456'}])
        self.assertEqual(called, [b'123456'])
        self.assertIsNone(status)

    async def test_slow_body_times_out(self):
        called, status = await self.run_request([], timeout=0.01, delay=1)
        self.assertFalse(called)
        self.assertEqual(status, 408)

    async def test_content_length_rejected_before_read(self):
        called, status = await self.run_request([], headers=[(b'content-length', b'100')])
        self.assertFalse(called)
        self.assertEqual(status, 413)

    async def test_invalid_content_length_is_controlled(self):
        for value in (b'-1', b'9' * 5000):
            called, status = await self.run_request([], headers=[(b'content-length', value)])
            self.assertFalse(called)
            self.assertEqual(status, 400)
