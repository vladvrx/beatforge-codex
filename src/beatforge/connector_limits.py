"""Bound authenticated JSON bodies before REST or MCP parsing."""
import anyio
from starlette.responses import JSONResponse

MAX_JSON_BYTES = 1024 * 1024


async def bounded_request(app, scope, receive, send):
    # PUT handlers already stream against an atomic, size-bounded blob reservation.
    if scope.get('method') == 'PUT':
        return await app(scope, receive, send)
    length = dict(scope.get('headers', [])).get(b'content-length')
    if length is not None and (not length.isdigit() or int(length) > MAX_JSON_BYTES):
        return await JSONResponse({'detail':'JSON request exceeds 1 MiB'}, status_code=413)(scope, receive, send)
    chunks, size = [], 0
    try:
        with anyio.fail_after(30):
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                chunk = message.get('body', b'')
                size += len(chunk)
                if size > MAX_JSON_BYTES:
                    return await JSONResponse({'detail':'JSON request exceeds 1 MiB'}, status_code=413)(scope, receive, send)
                chunks.append(chunk)
                if not message.get('more_body', False):
                    break
    except TimeoutError:
        return await JSONResponse({'detail':'Request body timed out'}, status_code=408)(scope, receive, send)
    body = b''.join(chunks)
    delivered = False
    async def replay():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {'type':'http.request', 'body':body, 'more_body':False}
        return await receive()
    await app(scope, replay, send)
