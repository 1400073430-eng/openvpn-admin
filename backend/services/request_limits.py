"""Bound request bodies before JSON parsing, including chunked requests."""
import asyncio
import time
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    def __init__(self, app, max_bytes=128 * 1024, timeout=10):
        self.app, self.max_bytes, self.timeout = app, max_bytes, timeout

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') not in ('POST', 'PUT', 'PATCH'):
            return await self.app(scope, receive, send)

        async def reject(status, detail):
            await JSONResponse({'detail': detail}, status_code=status)(scope, receive, send)

        lengths = [value for key, value in scope.get('headers', []) if key.lower() == b'content-length']
        if lengths:
            if len(lengths) != 1 or len(lengths[0]) > 10 or not lengths[0].isdigit():
                return await reject(400, '请求长度无效')
            if int(lengths[0]) > self.max_bytes:
                return await reject(413, '请求体过大')
        body = bytearray()
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                message = await asyncio.wait_for(receive(), remaining)
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                if len(body) + len(chunk) > self.max_bytes:
                    return await reject(413, '请求体过大')
                body.extend(chunk)
                if not message.get('more_body', False):
                    break
        except asyncio.TimeoutError:
            return await reject(408, '请求体接收超时')
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
