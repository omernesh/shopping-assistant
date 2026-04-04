"""Media handler for voice messages and images in Telegram."""
from __future__ import annotations

import base64
import json
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

VISION_MODEL = "gemini-2.5-flash-lite"
VISION_MAX_TOKENS = 200
GEMINI_VISION_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

VISION_SYSTEM_PROMPT = (
    "אתה מזהה מוצרים לרשימת קניות. "
    "זהה רק את המוצר העיקרי בתמונה — זה שמישהו מחזיק, מצלם מקרוב, או מציג בכוונה. "
    "התעלם מרקע, רהיטים וחפצים אחרים. "
    "אם אתה רואה ברקוד בתמונה, תחזיר רק את מספר הברקוד עם הקידומת BARCODE: (לדוגמה: BARCODE:7290000123456). "
    "אחרת, תחזיר רק את שם המוצר בעברית, מילה אחת עד שלוש, בלי הסברים. "
    "אם לא ניתן לזהות מוצר וגם אין ברקוד, תחזיר: לא הצלחתי לזהות את המוצר"
)

RECEIPT_PARSE_PROMPT = (
    "Extract all line items from this receipt/bill. Return a JSON array where each item has:\n"
    '- "name": product name in Hebrew\n'
    '- "price": final price as number (after discounts)\n'
    '- "quantity": quantity as number (default 1)\n'
    '- "sku": product SKU/barcode if visible\n'
    '- "store_name": store name if visible at top of receipt\n'
    '- "chain_name": chain name if visible (שופרסל, רמי לוי, etc.)\n'
    "\n"
    "Return ONLY the JSON array, no other text. Example:\n"
    '[{"name": "חלב תנובה 3%", "price": 6.90, "quantity": 1, "sku": "7290000001234", '
    '"store_name": "שופרסל דיל חולון", "chain_name": "שופרסל"}]'
)

RECEIPT_VISION_MODEL = "gemini-2.5-flash-lite"
# Issue #12: Increase max tokens from 2000 to 4000 for long receipts
RECEIPT_MAX_TOKENS = 4000


class MediaHandler:
    def __init__(
        self,
        telegram_token: str,
        soniox_api_key: str | None = None,
        gemini_api_key: str | None = None,
    ):
        self.telegram_token = telegram_token
        self.soniox_api_key = soniox_api_key
        self.gemini_api_key = gemini_api_key
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

    def download_photo(self, file_id: str) -> bytes | None:
        """Download a photo from Telegram. Public wrapper for _download_telegram_file."""
        try:
            return self._download_telegram_file(file_id)
        except Exception as exc:
            logger.exception("Failed to download photo %s: %s", file_id, exc)
            return None

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
            deadline = time.monotonic() + SONIOX_MAX_WAIT
            completed = False
            while time.monotonic() < deadline:
                time.sleep(SONIOX_POLL_INTERVAL)
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
                logger.error("Soniox transcription timed out after %ds (id=%s)", SONIOX_MAX_WAIT, transcription_id)
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

    # -- Gemini Vision --

    def identify_product_image(self, file_id: str) -> str | None:
        """Download photo from Telegram and identify product via Gemini vision."""
        if not self.gemini_api_key:
            logger.warning("GEMINI_API_KEY not configured, skipping image recognition")
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

        prompt_text = "מה המוצר בתמונה?"

        try:
            url = GEMINI_VISION_URL.format(model=VISION_MODEL)
            resp = self.session.post(
                url,
                params={"key": self.gemini_api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "system_instruction": {"parts": [{"text": VISION_SYSTEM_PROMPT}]},
                    "contents": [{
                        "parts": [
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}},
                            {"text": prompt_text},
                        ],
                    }],
                    "generationConfig": {"maxOutputTokens": VISION_MAX_TOKENS},
                },
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                logger.error("Gemini vision returned no candidates (file_id=%s): %s", file_id, data)
                return None
            parts = candidates[0].get("content", {}).get("parts", [])
            text = " ".join(p.get("text", "") for p in parts).strip()
            if not text:
                logger.warning("Gemini vision returned empty text (file_id=%s)", file_id)
                return None
            logger.info("Vision result (Gemini): %s", text[:100])
            return text

        except requests.RequestException as exc:
            logger.error("Gemini vision network error (file_id=%s): %s", file_id, exc)
            return None
        except (KeyError, ValueError) as exc:
            logger.error("Gemini vision response parse error (file_id=%s): %s", file_id, exc)
            return None
        except Exception as exc:
            logger.exception("Unexpected error in Gemini vision (file_id=%s): %s", file_id, exc)
            return None

    # -- Receipt parsing --

    def parse_receipt(self, image_bytes: bytes) -> list[dict[str, Any]]:
        """Send receipt photo to Gemini Flash and extract line items.

        Returns a list of dicts with keys: name, price, quantity, sku, store_name, chain_name.
        Returns empty list on failure.
        """
        if not self.gemini_api_key:
            logger.warning("GEMINI_API_KEY not configured, skipping receipt parsing")
            return []

        if not image_bytes:
            logger.error("Empty image bytes for receipt parsing")
            return []

        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        try:
            url = GEMINI_VISION_URL.format(model=RECEIPT_VISION_MODEL)
            resp = self.session.post(
                url,
                params={"key": self.gemini_api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{
                        "parts": [
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64_image}},
                            {"text": RECEIPT_PARSE_PROMPT},
                        ],
                    }],
                    "generationConfig": {"maxOutputTokens": RECEIPT_MAX_TOKENS},
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                logger.error("Gemini receipt parse returned no candidates")
                return []
            parts = candidates[0].get("content", {}).get("parts", [])
            raw_text = " ".join(p.get("text", "") for p in parts).strip()
            if not raw_text:
                logger.warning("Gemini receipt parse returned empty text")
                return []

            logger.info("Receipt parse raw: %s", raw_text[:200])

            # Extract JSON array from response (may have markdown fences)
            json_text = raw_text
            if "```" in json_text:
                lines = json_text.split("\n")
                inside = False
                json_lines = []
                for line in lines:
                    if line.strip().startswith("```"):
                        inside = not inside
                        continue
                    if inside:
                        json_lines.append(line)
                json_text = "\n".join(json_lines)

            # Find the JSON array boundaries
            start = json_text.find("[")
            end = json_text.rfind("]")
            if start == -1 or end == -1 or end <= start:
                logger.error("Receipt parse: no JSON array found in: %s", raw_text[:200])
                return []

            items = json.loads(json_text[start:end + 1])
            if not isinstance(items, list):
                logger.error("Receipt parse: expected list, got %s", type(items).__name__)
                return []

            # Issue #11: Per-item parsing with try/except -- skip bad items instead of aborting
            result = []
            for item in items:
                try:
                    if not isinstance(item, dict):
                        continue
                    result.append({
                        "name": str(item.get("name", "")).strip(),
                        "price": float(item.get("price", 0) or 0),
                        "quantity": float(item.get("quantity", 1) or 1),
                        "sku": str(item.get("sku", "") or "").strip(),
                        "store_name": str(item.get("store_name", "") or "").strip(),
                        "chain_name": str(item.get("chain_name", "") or "").strip(),
                    })
                except (ValueError, TypeError):
                    continue  # skip malformed items

            logger.info("Receipt parsed: %d items", len(result))
            return result

        except json.JSONDecodeError as exc:
            logger.error("Receipt parse JSON error: %s", exc)
            return []
        except requests.RequestException as exc:
            logger.error("Gemini receipt parse network error: %s", exc)
            return []
        except (KeyError, ValueError) as exc:
            logger.error("Gemini receipt parse response error: %s", exc)
            return []
        except Exception as exc:
            logger.exception("Unexpected error in receipt parsing: %s", exc)
            return []

    # -- Warmup --

    def warmup_vision(self) -> None:
        """Send a tiny request to Gemini to warm up the model (avoid cold start)."""
        if not self.gemini_api_key:
            return
        try:
            url = GEMINI_VISION_URL.format(model=VISION_MODEL)
            requests.post(
                url,
                params={"key": self.gemini_api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": "hi"}]}],
                    "generationConfig": {"maxOutputTokens": 1},
                },
                timeout=120,
            )
            logger.info("Gemini vision model warmed up")
        except Exception as exc:
            logger.warning("Gemini warmup failed (non-critical): %s", exc)

    # -- Combined processing --

    def process_voice_message(self, voice_file_id: str) -> str | None:
        """Process a voice message and return transcribed text."""
        return self.transcribe_voice(voice_file_id)

    def process_photo_message(
        self, photo_file_id: str, caption: str | None = None,
    ) -> str | None:
        """Process a photo and return product identification or action text."""
        product = self.identify_product_image(photo_file_id)
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
