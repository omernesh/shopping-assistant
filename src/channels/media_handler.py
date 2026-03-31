"""Media handler for voice messages and images in Telegram."""
from __future__ import annotations

import base64
import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

SONIOX_UPLOAD_URL = "https://api.soniox.com/v1/files"
SONIOX_TRANSCRIBE_URL = "https://api.soniox.com/v1/transcriptions"
SONIOX_MODEL = "stt-async-preview"
SONIOX_POLL_INTERVAL = 1.0
SONIOX_MAX_WAIT = 60

VISION_MODEL = "MiniMax-M2.7"
VISION_MAX_TOKENS = 200
VISION_API_URL = "https://api.minimax.io/anthropic/v1/messages"

VISION_SYSTEM_PROMPT = (
    "אתה מזהה מוצרים בתמונות. "
    "תחזיר רק את שם המוצר בעברית, בלי הסברים. "
    "אם יש כמה מוצרים, תפריד בפסיקים. "
    "אם לא ניתן לזהות מוצר, תחזיר: לא הצלחתי לזהות את המוצר"
)


class MediaHandler:
    def __init__(
        self,
        telegram_token: str,
        soniox_api_key: str | None = None,
        vision_api_key: str | None = None,
        vision_api_url: str = "https://api.minimax.io/anthropic/v1/messages",
    ):
        self.telegram_token = telegram_token
        self.soniox_api_key = soniox_api_key
        self.vision_api_key = vision_api_key
        self.vision_api_url = vision_api_url
        self.tg_base = f"https://api.telegram.org/bot{telegram_token}"
        self.session = requests.Session()

    # -- Telegram file download --

    def _download_telegram_file(self, file_id: str) -> bytes:
        """Download a file from Telegram by file_id."""
        resp = self.session.get(
            f"{self.tg_base}/getFile",
            params={"file_id": file_id},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getFile failed: {payload.get('description', payload)}")
        file_path = payload["result"]["file_path"]
        dl_url = f"https://api.telegram.org/file/bot{self.telegram_token}/{file_path}"
        dl_resp = self.session.get(dl_url, timeout=30)
        dl_resp.raise_for_status()
        return dl_resp.content

    # -- Soniox STT --

    def transcribe_voice(self, file_id: str) -> str | None:
        """Download voice from Telegram and transcribe via Soniox async API."""
        if not self.soniox_api_key:
            logger.warning("SONIOX_API_KEY not configured, skipping transcription")
            return None

        try:
            audio_bytes = self._download_telegram_file(file_id)
        except Exception as exc:
            logger.exception("Failed to download voice file %s: %s", file_id, exc)
            return None

        if not audio_bytes:
            logger.error("Downloaded voice file is empty (file_id=%s)", file_id)
            return None

        headers = {"Authorization": f"Bearer {self.soniox_api_key}"}

        try:
            # Step 1: Upload file
            upload_resp = self.session.post(
                SONIOX_UPLOAD_URL,
                headers=headers,
                files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                timeout=30,
            )
            upload_resp.raise_for_status()
            uploaded_file_id = upload_resp.json()["id"]

            # Step 2: Create transcription
            create_resp = self.session.post(
                SONIOX_TRANSCRIBE_URL,
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "model": SONIOX_MODEL,
                    "file_id": uploaded_file_id,
                    "language_hints": ["he", "en"],
                },
                timeout=15,
            )
            create_resp.raise_for_status()
            transcription_id = create_resp.json()["id"]

            # Step 3: Poll for completion
            elapsed = 0.0
            completed = False
            while elapsed < SONIOX_MAX_WAIT:
                time.sleep(SONIOX_POLL_INTERVAL)
                elapsed += SONIOX_POLL_INTERVAL
                status_resp = self.session.get(
                    f"{SONIOX_TRANSCRIBE_URL}/{transcription_id}",
                    headers=headers,
                    timeout=15,
                )
                status_resp.raise_for_status()
                status = status_resp.json().get("status")
                if status == "completed":
                    completed = True
                    break
                if status == "error":
                    err = status_resp.json().get("error_message", "unknown")
                    logger.error("Soniox transcription error: %s", err)
                    return None

            if not completed:
                logger.error("Soniox transcription timed out after %.0fs (id=%s)", elapsed, transcription_id)
                return None

            # Step 4: Get transcript
            transcript_resp = self.session.get(
                f"{SONIOX_TRANSCRIBE_URL}/{transcription_id}/transcript",
                headers=headers,
                timeout=15,
            )
            transcript_resp.raise_for_status()
            text = transcript_resp.json().get("text", "").strip()
            logger.info("Soniox transcription result: %s", text[:100])
            return text if text else None

        except requests.RequestException as exc:
            logger.error("Soniox network error (file_id=%s): %s", file_id, exc)
            return None
        except (KeyError, ValueError) as exc:
            logger.error("Soniox response parse error (file_id=%s): %s", file_id, exc)
            return None
        except Exception as exc:
            logger.exception("Unexpected error in Soniox transcription (file_id=%s): %s", file_id, exc)
            return None

    # -- OpenAI Vision --

    def identify_product_image(self, file_id: str, caption: str | None = None) -> str | None:
        """Download photo from Telegram and identify product via GPT vision."""
        if not self.vision_api_key:
            logger.warning("Vision API key not configured, skipping image recognition")
            return None

        try:
            image_bytes = self._download_telegram_file(file_id)
        except Exception as exc:
            logger.exception("Failed to download photo %s: %s", file_id, exc)
            return None

        if not image_bytes:
            logger.error("Downloaded photo is empty (file_id=%s)", file_id)
            return None

        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        # Build Anthropic messages API content (MiniMax Anthropic-compatible)
        anthropic_content = []
        anthropic_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": b64_image,
            },
        })
        prompt_text = caption if caption else "מה המוצר בתמונה?"
        anthropic_content.append({"type": "text", "text": prompt_text})

        try:
            resp = self.session.post(
                self.vision_api_url,
                headers={
                    "x-api-key": self.vision_api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                },
                json={
                    "model": VISION_MODEL,
                    "max_tokens": VISION_MAX_TOKENS,
                    "system": VISION_SYSTEM_PROMPT,
                    "messages": [
                        {"role": "user", "content": anthropic_content},
                    ],
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content_blocks = data.get("content")
            if not content_blocks:
                logger.error("Vision API returned no content (file_id=%s): %s", file_id, data.get("error", data))
                return None
            # Extract text from content blocks
            text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
            text = " ".join(text_parts).strip()
            if not text:
                logger.warning("Vision API returned empty text (file_id=%s)", file_id)
                return None
            logger.info("Vision result (MiniMax): %s", text[:100])
            return text

        except requests.RequestException as exc:
            logger.error("Vision API network error (file_id=%s): %s", file_id, exc)
            return None
        except (KeyError, ValueError) as exc:
            logger.error("Vision API response parse error (file_id=%s): %s", file_id, exc)
            return None
        except Exception as exc:
            logger.exception("Unexpected error in vision (file_id=%s): %s", file_id, exc)
            return None

    # -- Combined processing --

    def process_voice_message(self, voice_file_id: str) -> str | None:
        """Process a voice message and return transcribed text."""
        return self.transcribe_voice(voice_file_id)

    def process_photo_message(
        self, photo_file_id: str, caption: str | None = None,
    ) -> str | None:
        """Process a photo and return product identification or action text."""
        product = self.identify_product_image(photo_file_id, caption=caption)
        if not product:
            return None

        # If there is a caption with action intent, combine them
        if caption:
            caption_text = caption.strip()
            # If caption has a placeholder like "this", replace with product name
            for placeholder in ["את זה", "אותו", "אותה", "זה"]:
                if placeholder in caption_text:
                    return caption_text.replace(placeholder, product, 1)
            # Caption has specific text -- return caption + product context
            return f"{caption_text} {product}"

        # No caption -- default to add action
        return product
