import json
from collections.abc import Iterator

import pytest
from starlette.types import Message, Receive, Scope, Send

from mc_manager.middleware import RequestSizeLimitMiddleware


@pytest.mark.asyncio
async def test_streaming_upload_limit_rejects_chunked_body() -> None:
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        del scope
        while True:
            message = await receive()
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages: Iterator[Message] = iter(
        [
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"67890", "more_body": False},
        ]
    )
    sent: list[Message] = []

    async def receive() -> Message:
        return next(messages)

    async def send(message: Message) -> None:
        sent.append(message)

    middleware = RequestSizeLimitMiddleware(app, max_bytes=8)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/maps",
            "headers": [],
        },
        receive,
        send,
    )

    assert sent[0]["status"] == 413
    payload = json.loads(sent[1]["body"])
    assert payload["error"]["code"] == "upload_too_large"
