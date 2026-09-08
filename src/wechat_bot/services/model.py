"""模型提供者：默认离线回显；HTTP 实现使用常见的 chat/completions 数据协议。"""

from pathlib import Path
from typing import Protocol

from wechat_bot.config import Settings
from wechat_bot.domain import ModelError
from wechat_bot.services.credentials import load_api_key
from wechat_bot.services.vision import user_content


class ReplyModel(Protocol):
    async def reply(self, prompt: str, history: list[dict[str, str]]) -> str: ...

    async def close(self) -> None: ...


class EchoModel:
    async def reply(self, prompt: str, history: list[dict[str, str]]) -> str:
        return f"收到：{prompt}"

    async def close(self) -> None:
        pass


class HttpModel:
    def __init__(self, settings: Settings):
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError('请先安装模型依赖：pip install -e ".[llm]"') from exc
        token = load_api_key(settings.api_key_env, settings.api_key_file)
        self.settings = settings
        # 不记录请求头；不跟随重定向，避免将授权头送往未知地址。
        self.client = httpx.AsyncClient(
            timeout=settings.model_timeout_seconds,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )

    async def reply(
        self, prompt: str, history: list[dict[str, str]], *, images: list[Path] | None = None,
    ) -> str:
        # TODO(T09)：当前仅限制历史轮数和字符数，尚无 token/费用预算及知识库检索。
        messages = [
            {"role": "system", "content": self.settings.system_prompt},
            *history,
            {"role": "user", "content": user_content(prompt, images)},
        ]
        try:
            response = await self.client.post(
                self.settings.base_url.rstrip("/") + "/chat/completions",
                json={"model": self.settings.model_name, "messages": messages, "stream": False,
                      "max_tokens": self.settings.model_max_tokens},
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty model reply")
            return content.strip()
        except Exception as exc:
            # 只暴露类型，不输出 HTTP 响应正文和可能包含敏感信息的异常描述。
            raise ModelError(type(exc).__name__) from None

    async def close(self) -> None:
        await self.client.aclose()
