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

VISION_MODEL = "hermes-agent"

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
        hermes_api_url: str = "http://localhost:8642",
    ):
        self.telegram_token = telegram_token
        self.soniox_api_key = soniox_api_key
        self.hermes_api_url = hermes_api_url.rstrip("/")
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
        file_path = resp.json()["result"]["file_path"]
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
                    break
                if status == "error":
                    err = status_resp.json().get("error_message", "unknown")
                    logger.error("Soniox transcription error: %s", err)
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

        except Exception as exc:
            logger.exception("Soniox transcription failed: %s", exc)
            return None

    # -- OpenAI Vision --

    def identify_product_image(self, file_id: str, caption: str | None = None) -> str | None:
        """Download photo from Telegram and identify product via GPT vision."""
        if not self.hermes_api_url:
            logger.warning("Hermes API URL not configured, skipping image recognition")
            return None

        try:
            image_bytes = self._download_telegram_file(file_id)
        except Exception as exc:
            logger.exception("Failed to download photo %s: %s", file_id, exc)
            return None

        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        user_content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
            },
        ]
        if caption:
            user_content.append({"type": "text", "text": caption})
        else:
            user_content.append({"type": "text", "text": "מה המוצר בתמונה?"})

        try:
            resp = self.session.post(
                f"{self.hermes_api_url}/v1/chat/completions",
                headers={"Content-Type": "application/json"},
                json={
                    "model": VISION_MODEL,
                    "max_tokens": 200,
                    "messages": [
                        {"role": "system", "content": VISION_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                },
                timeout=60,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info("Vision result (via Hermes): %s", text[:100])
            return text if text else None

        except Exception as exc:
            logger.exception("Hermes vision request failed: %s", exc)
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
            caption_lower = caption.strip()
            # If caption has a placeholder like "this", replace with product name
            for placeholder in ["את זה", "אותו", "אותה", "זה"]:
                if placeholder in caption_lower:
                    return caption_lower.replace(placeholder, product, 1)
            # Caption has specific text -- return caption + product context
            return f"{caption_lower} {product}"

        # No caption -- default to add action
        return product
