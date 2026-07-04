"""Volcano Engine TTS — text-to-speech for Soft-Mianmian (团子 voice)."""
import base64
import uuid

import requests


class TTSError(Exception):
    """TTS provider failure."""


class VolcanoTTS:
    """Volcano Engine (ByteDance) Text-to-Speech.

    Uses X-Api-Key header auth + App ID / Access Token in body.
    Voice types for cute young style:
    - zh_female_tianmei  (甜美)
    - zh_female_qingxin  (清新)
    """

    BASE_URL = "https://openspeech.bytedance.com/api/v1/tts"

    def __init__(
        self,
        api_key: str,
        app_id: str = "",
        access_token: str = "",
        voice_type: str = "zh_female_tianmei",
    ):
        if not api_key:
            raise TTSError("Volcano TTS requires api_key")
        self._api_key = api_key
        self._app_id = app_id
        self._access_token = access_token or api_key
        self._voice = voice_type

    def synthesize(self, text: str, speed: float = 1.0, volume: float = 1.0) -> bytes:
        """Convert text to MP3 audio bytes."""
        if not text.strip():
            raise TTSError("Empty text")

        reqid = str(uuid.uuid4())

        payload = {
            "app": {
                "appid": self._app_id,
                "token": self._access_token,
                "cluster": "volcano_tts",
            },
            "user": {
                "uid": "soft_mianmian",
            },
            "audio": {
                "voice_type": self._voice,
                "encoding": "mp3",
                "speed_ratio": speed,
                "volume_ratio": volume,
            },
            "request": {
                "reqid": reqid,
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

        try:
            resp = requests.post(
                self.BASE_URL,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer;{self._access_token}",
                },
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()

            # Check error code
            code = body.get("code", 3000)
            if code != 3000:
                msg = body.get("message", "unknown")
                raise TTSError(f"TTS API error {code}: {msg}")

            # Parse audio: response has "data" field with base64 directly
            audio_b64 = body.get("data", "")
            if not audio_b64:
                raise TTSError(f"No audio data in response: {str(body)[:200]}")

            return base64.b64decode(audio_b64)

        except requests.Timeout:
            raise TTSError("TTS request timed out")
        except requests.HTTPError as exc:
            raise TTSError(f"TTS HTTP {exc.response.status_code}: {exc.response.text[:200]}")
        except requests.RequestException as exc:
            raise TTSError(f"TTS network error: {exc}")
        except (KeyError, ValueError, TypeError) as exc:
            raise TTSError(f"TTS parse error: {exc}")
