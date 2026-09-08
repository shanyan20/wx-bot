import asyncio
import base64
import json
from dataclasses import replace

import httpx
import pytest

from wechat_bot.services.credentials import load_api_key
from wechat_bot.services.model import HttpModel
from wechat_bot.services.vision import image_data_url, user_content


def test_environment_credential_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("TEST_MODEL_KEY", "fake-test-only")
    assert load_api_key("TEST_MODEL_KEY", tmp_path / "missing.dpapi") == "fake-test-only"


def test_missing_credential_does_not_include_secret(monkeypatch):
    monkeypatch.delenv("TEST_MODEL_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_MODEL_KEY"):
        load_api_key("TEST_MODEL_KEY", None)


def test_dpapi_loader_boundary_without_user_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("TEST_MODEL_KEY", raising=False)
    monkeypatch.setattr("wechat_bot.services.credentials.unprotect", lambda _: "fake-encrypted-key")
    assert load_api_key("TEST_MODEL_KEY", tmp_path / "fake.dpapi") == "fake-encrypted-key"


def test_png_detected_by_bytes_not_extension(tmp_path):
    path = tmp_path / "image.bin"
    data = b"\x89PNG\r\n\x1a\n" + b"synthetic header fixture"
    path.write_bytes(data)
    url = image_data_url(path)
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == data


def test_non_image_file_rejected(tmp_path):
    path = tmp_path / "fake.png"
    path.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError, match="文件头"):
        image_data_url(path)


def test_oversized_image_rejected_before_upload(tmp_path, monkeypatch):
    monkeypatch.setattr("wechat_bot.services.vision.MAX_IMAGE_BYTES", 8)
    path = tmp_path / "big.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nmore data")
    with pytest.raises(ValueError, match="MiB"):
        image_data_url(path)


def test_too_many_images_rejected_before_reading(tmp_path):
    with pytest.raises(ValueError, match="最多"):
        user_content("question", [tmp_path / "missing.png"] * 5)


def test_multimodal_model_request(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_API_KEY", "test-secret")
    path = tmp_path / "image.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic header fixture")

    async def scenario():
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "a picture"}}]})

        model = HttpModel(replace(
            settings, base_url="https://model.test", model_name="deepseek-v4-flash-vision-exp",
        ))
        await model.client.aclose()
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            assert await model.reply("what is here", [], images=[path]) == "a picture"
            request = requests[0]
            assert request["model"] == "deepseek-v4-flash-vision-exp"
            content = request["messages"][-1]["content"]
            assert content[0] == {"type": "text", "text": "what is here"}
            assert content[1]["type"] == "image_url"
            assert request["max_tokens"] == settings.model_max_tokens
        finally:
            await model.close()
    asyncio.run(scenario())

