"""
LLM abstraction for PEAR.

Providers are swappable; agents only depend on BaseLLM.
Default provider is Ollama (local). OpenAI / Anthropic are optional.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional


@dataclass
class LLMMessage:
    role: str  # system | user | assistant
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    provider: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)


class BaseLLM(ABC):
    """Interface every provider implements."""

    provider: str = "base"

    def __init__(self, model: str, **kwargs):
        self.model = model
        self.kwargs = kwargs

    @abstractmethod
    def generate(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> LLMResponse:
        ...

    def chat(
        self,
        system: str,
        user: str,
        history: Optional[List[LLMMessage]] = None,
        **kwargs,
    ) -> LLMResponse:
        """Convenience: system + optional history + user → response."""
        messages: List[LLMMessage] = []
        if system:
            messages.append(LLMMessage(role="system", content=system))
        if history:
            messages.extend(history)
        messages.append(LLMMessage(role="user", content=user))
        return self.generate(messages, **kwargs)

    def is_available(self) -> bool:
        """Quick health check. Override in subclasses."""
        return True

    def generate_stream(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        on_token=None,
    ) -> LLMResponse:
        """
        Default fallback: no real token streaming – runs generate() and
        delivers the full text as a single callback. Providers that support
        real incremental streaming override this.
        """
        response = self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        if on_token is not None:
            on_token(response.content)
        return response

    def chat_stream(
        self,
        system: str,
        user: str,
        history: Optional[List[LLMMessage]] = None,
        on_token=None,
        **kwargs,
    ) -> LLMResponse:
        """Convenience: system + optional history + user → streamed response."""
        messages: List[LLMMessage] = []
        if system:
            messages.append(LLMMessage(role="system", content=system))
        if history:
            messages.extend(history)
        messages.append(LLMMessage(role="user", content=user))
        return self.generate_stream(messages, on_token=on_token, **kwargs)


# ── Ollama ────────────────────────────────────────────────────────

class OllamaLLM(BaseLLM):
    """
    Local models via Ollama (https://github.com/ollama/ollama).
    Default endpoint: http://localhost:11434
    """

    provider = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        **kwargs,
    ):
        super().__init__(model, **kwargs)
        self.base_url = base_url.rstrip("/")

    def generate(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": stream,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        url = f"{self.base_url}/api/chat"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama unreachable at {self.base_url}. "
                f"Is `ollama serve` running? ({e})"
            ) from e

        content = ""
        if "message" in body:
            content = body["message"].get("content", "")
        elif "response" in body:
            content = body["response"]

        return LLMResponse(
            content=content.strip(),
            model=body.get("model", self.model),
            provider=self.provider,
            raw=body,
            usage={
                "prompt_eval_count": body.get("prompt_eval_count"),
                "eval_count": body.get("eval_count"),
            },
        )

    def is_available(self) -> bool:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate_stream(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        on_token=None,
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "stream": True,
            "options": {"temperature": temperature},
        }
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens

        url = f"{self.base_url}/api/chat"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST",
        )

        full_text = []
        final_body: Dict[str, Any] = {}
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    delta = chunk.get("message", {}).get("content", "")
                    if delta:
                        full_text.append(delta)
                        if on_token is not None:
                            on_token(delta)
                    if chunk.get("done"):
                        final_body = chunk
                        break
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Ollama unreachable at {self.base_url}. Is `ollama serve` running? ({e})"
            ) from e

        return LLMResponse(
            content="".join(full_text).strip(),
            model=final_body.get("model", self.model),
            provider=self.provider,
            raw=final_body,
            usage={
                "prompt_eval_count": final_body.get("prompt_eval_count"),
                "eval_count": final_body.get("eval_count"),
            },
        )

    def list_models(self) -> List[str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []


# ── OpenAI ────────────────────────────────────────────────────────

class OpenAILLM(BaseLLM):
    provider = "openai"

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        **kwargs,
    ):
        super().__init__(model, **kwargs)
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def generate(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"OpenAI request failed: {e}") from e

        content = body["choices"][0]["message"]["content"]
        return LLMResponse(
            content=content.strip(),
            model=body.get("model", self.model),
            provider=self.provider,
            raw=body,
            usage=body.get("usage", {}),
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate_stream(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        on_token=None,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        full_text = []
        model_name = self.model
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:"):].strip()
                    if payload_str == "[DONE]":
                        break
                    chunk = json.loads(payload_str)
                    model_name = chunk.get("model", model_name)
                    choices = chunk.get("choices") or [{}]
                    delta = choices[0].get("delta", {}).get("content", "")
                    if delta:
                        full_text.append(delta)
                        if on_token is not None:
                            on_token(delta)
        except urllib.error.URLError as e:
            raise RuntimeError(f"OpenAI request failed: {e}") from e

        return LLMResponse(
            content="".join(full_text).strip(),
            model=model_name,
            provider=self.provider,
        )


# ── Anthropic ─────────────────────────────────────────────────────

class AnthropicLLM(BaseLLM):
    provider = "anthropic"

    def __init__(
        self,
        model: str = "claude-3-5-haiku-20241022",
        api_key: Optional[str] = None,
        base_url: str = "https://api.anthropic.com/v1",
        **kwargs,
    ):
        super().__init__(model, **kwargs)
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    def generate(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        system = ""
        chat_msgs = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                chat_msgs.append(m.to_dict())

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": chat_msgs,
            "max_tokens": max_tokens or 2048,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as e:
            raise RuntimeError(f"Anthropic request failed: {e}") from e

        content = ""
        for block in body.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")

        return LLMResponse(
            content=content.strip(),
            model=body.get("model", self.model),
            provider=self.provider,
            raw=body,
            usage=body.get("usage", {}),
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate_stream(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        on_token=None,
    ) -> LLMResponse:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        system = ""
        chat_msgs = []
        for m in messages:
            if m.role == "system":
                system = m.content
            else:
                chat_msgs.append(m.to_dict())

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": chat_msgs,
            "max_tokens": max_tokens or 2048,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        full_text = []
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    payload_str = line[len("data:"):].strip()
                    if not payload_str:
                        continue
                    event = json.loads(payload_str)
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta", {}).get("text", "")
                        if delta:
                            full_text.append(delta)
                            if on_token is not None:
                                on_token(delta)
                    elif event.get("type") == "message_stop":
                        break
        except urllib.error.URLError as e:
            raise RuntimeError(f"Anthropic request failed: {e}") from e

        return LLMResponse(
            content="".join(full_text).strip(),
            model=self.model,
            provider=self.provider,
        )


# ── Fallback (no model) ───────────────────────────────────────────

class EchoLLM(BaseLLM):
    """Used when no real provider is available – keeps PEAR runnable."""

    provider = "echo"

    def __init__(self, model: str = "echo", **kwargs):
        super().__init__(model, **kwargs)

    def generate(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> LLMResponse:
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(
            content=(
                f"[echo mode – no LLM connected]\n"
                f"You said: “{user}”\n"
                f"Start Ollama (`ollama serve` + `ollama pull llama3.2`) "
                f"or set OPENAI_API_KEY / ANTHROPIC_API_KEY."
            ),
            model=self.model,
            provider=self.provider,
        )

    def generate_stream(
        self,
        messages: List[LLMMessage],
        *,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        on_token=None,
    ) -> LLMResponse:
        response = self.generate(messages, temperature=temperature, max_tokens=max_tokens)
        if on_token is not None:
            words = response.content.split(" ")
            for i, word in enumerate(words):
                on_token(word if i == 0 else " " + word)
        return response


# ── Factory ───────────────────────────────────────────────────────

def create_llm(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> BaseLLM:
    """
    Build an LLM from env / arguments.

    Env vars:
      PEAR_LLM_PROVIDER  = ollama | openai | anthropic | echo
      PEAR_LLM_MODEL     = model name
      OLLAMA_HOST        = http://localhost:11434
      OPENAI_API_KEY, ANTHROPIC_API_KEY
    """
    provider = (provider or os.environ.get("PEAR_LLM_PROVIDER", "ollama")).lower()
    model = model or os.environ.get("PEAR_LLM_MODEL")

    if provider == "ollama":
        base = kwargs.pop("base_url", os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
        llm = OllamaLLM(model=model or "llama3.2", base_url=base, **kwargs)
        if llm.is_available():
            return llm
        # fall through to echo if ollama isn't running
        return EchoLLM()

    if provider == "openai":
        return OpenAILLM(model=model or "gpt-4o-mini", **kwargs)

    if provider == "anthropic":
        return AnthropicLLM(model=model or "claude-3-5-haiku-20241022", **kwargs)

    if provider == "echo":
        return EchoLLM()

    raise ValueError(f"Unknown LLM provider: {provider}")
