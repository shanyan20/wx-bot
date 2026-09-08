import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from wechat_bot.domain import ModelError
from wechat_bot.services.model import HttpModel


def test_http_payload_and_reply_without_network(settings, monkeypatch):
    monkeypatch.setenv("BOT_API_KEY", "test-secret")

    async def scenario():
        received = []

        def respond(request):
            received.append(request)
            return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}]})

        model = HttpModel(replace(settings, base_url="https://model.test/v1", model_name="test"))
        assert model.client.headers["Authorization"] == "Bearer test-secret"
        await model.client.aclose()
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            reply = await model.reply("问题", [{"role": "user", "content": "过去的问题"}])
            assert reply == "你好"
            request = received[0]
            assert str(request.url) == "https://model.test/v1/chat/completions"
            payload = json.loads(request.content)
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][-1] == {"role": "user", "content": "问题"}
            assert payload["stream"] is False
        finally:
            await model.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("response", [
    httpx.Response(429, text="private server response"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
])
def test_http_errors_sanitized(settings, monkeypatch, response):
    monkeypatch.setenv("BOT_API_KEY", "test-secret")

    async def scenario():
        model = HttpModel(replace(settings, base_url="https://model.test/v1", model_name="test"))
        await model.client.aclose()
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response))
        try:
            with pytest.raises(ModelError) as error:
                await model.reply("private prompt", [])
            assert "private" not in str(error.value)
            assert "test-secret" not in str(error.value)
        finally:
            await model.close()
    asyncio.run(scenario())
