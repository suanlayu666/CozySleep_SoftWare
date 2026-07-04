"""Pluggable AI provider — DeepSeek now, voice/multimodal later."""
from abc import ABC, abstractmethod

import requests


class AIError(Exception):
    """Wrapper for all AI provider failures (network, auth, timeout, bad response)."""


class AIProvider(ABC):
    """Abstract interface for AI chat backends."""

    @abstractmethod
    def chat(self, messages: list[dict], **kwargs) -> str:
        """Send a conversation to the AI, return the reply text.

        Args:
            messages: List of {"role": "system"|"user"|"assistant", "content": str}
            **kwargs: provider-specific overrides (temperature, max_tokens, timeout)

        Returns:
            The AI's text reply.

        Raises:
            AIError: On any failure (network, auth, timeout, bad response).
        """


class DeepSeekProvider(AIProvider):
    """OpenAI-compatible chat client for DeepSeek (and similar APIs)."""

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "deepseek-chat",
    ):
        if not api_base or not api_key:
            raise AIError("DeepSeekProvider requires api_base and api_key")
        self._base = api_base.rstrip("/")
        self._key = api_key
        self._model = model

    def chat(self, messages, temperature=0.7, max_tokens=500, timeout=15, **kwargs):
        """Send a chat request to DeepSeek.

        Args:
            messages: List[dict] — conversation messages.
            temperature: 0.0–2.0, controls randomness.
            max_tokens: Max tokens in the response.
            timeout: Request timeout in seconds.

        Returns:
            The assistant's text reply.

        Raises:
            AIError: Wraps any underlying failure.
        """
        try:
            resp = requests.post(
                f"{self._base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            body = resp.json()
            return body["choices"][0]["message"]["content"].strip()
        except requests.Timeout:
            raise AIError(f"AI request timed out after {timeout}s")
        except requests.HTTPError as exc:
            raise AIError(f"AI HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        except (KeyError, IndexError, ValueError) as exc:
            raise AIError(f"AI response parse error: {exc}")
        except requests.RequestException as exc:
            raise AIError(f"AI network error: {exc}")
