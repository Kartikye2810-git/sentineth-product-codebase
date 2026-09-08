"""Bound the entire body before multipart parsing, even without Content-Length."""
import asyncio
import tempfile

from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from app.settings import get_settings


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        upload = scope["method"] == "POST" and scope["path"].endswith("/documents")
        maximum = get_settings().max_upload_bytes + 1024 * 1024 if upload else 16 * 1024
        headers = dict(scope.get("headers", []))
        async def reject():
            response = JSONResponse({"detail": {"error_code": "INPUT_TOO_LARGE",
                "message": "Request body exceeds the size limit."}}, status_code=413)
            await response(scope, receive, send)
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await reject()
        if declared < 0 or declared > maximum:
            return await reject()
        # Spill to disk after 64KB. This also bounds the parser's temporary
        # upload before route dependencies or UploadFile.file are available.
        with tempfile.SpooledTemporaryFile(max_size=64 * 1024) as body:
            total = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                block = message.get("body", b"")
                total += len(block)
                if total > maximum:
                    return await reject()
                await asyncio.to_thread(body.write, block)
                if not message.get("more_body", False):
                    break
            await asyncio.to_thread(body.seek, 0)
            consumed = False
            async def replay():
                nonlocal consumed
                if consumed:
                    return await receive()
                block = await asyncio.to_thread(body.read, 64 * 1024)
                more = body.tell() < total
                consumed = not more
                return {"type": "http.request", "body": block, "more_body": more}
            try:
                await self.app(scope, replay, send)
            except ClientDisconnect:
                return
