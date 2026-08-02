"""Vision providers: OCR, image describe, offline fallback."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from .base import BaseVision, VisionResult


class OfflineVision(BaseVision):
    provider = "offline"

    def ocr(self, image_path: Path, **kwargs) -> VisionResult:
        path = Path(image_path)
        sidecar = path.with_suffix(path.suffix + ".txt")
        if not sidecar.exists():
            sidecar = path.with_suffix(".ocr.txt")
        if sidecar.exists():
            text = sidecar.read_text(encoding="utf-8", errors="replace")
            return VisionResult(text=text, description="Sidecar OCR text", provider=self.provider)
        return VisionResult(
            text="",
            description=f"[offline vision] No OCR engine for {path.name}",
            provider=self.provider,
        )

    def describe(self, image_path: Path, prompt: str = "", **kwargs) -> VisionResult:
        path = Path(image_path)
        return VisionResult(
            text="",
            description=f"[offline vision] Cannot describe {path.name} without a vision model. Prompt: {prompt[:80]}",
            labels=[],
            provider=self.provider,
        )


class TesseractOCR(BaseVision):
    provider = "tesseract"

    def is_available(self) -> bool:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except Exception:
            return False

    def ocr(self, image_path: Path, **kwargs) -> VisionResult:
        import pytesseract
        from PIL import Image
        img = Image.open(image_path)
        text = pytesseract.image_to_string(img)
        return VisionResult(text=text.strip(), description="Tesseract OCR", provider=self.provider)

    def describe(self, image_path: Path, prompt: str = "", **kwargs) -> VisionResult:
        # OCR-only provider: describe via extracted text summary
        o = self.ocr(image_path)
        words = o.text.split()
        desc = f"Image contains ~{len(words)} words of text." if words else "No text detected."
        return VisionResult(text=o.text, description=desc, labels=["ocr"], provider=self.provider)


class MultimodalLLMVision(BaseVision):
    """Uses an OpenAI-compatible vision chat endpoint when configured."""

    provider = "multimodal_llm"

    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4o-mini"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model

    def is_available(self) -> bool:
        return bool(self.api_key)

    def ocr(self, image_path: Path, **kwargs) -> VisionResult:
        return self.describe(image_path, prompt="Extract all text from this image (OCR).")

    def describe(self, image_path: Path, prompt: str = "", **kwargs) -> VisionResult:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not set")
        # Prefer local offline if requests/vision not practical in sandbox
        try:
            import base64
            import json
            import urllib.request
            data = Path(image_path).read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            suffix = Path(image_path).suffix.lower().lstrip(".") or "png"
            mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
            user_prompt = prompt or "Describe this image in detail."
            payload = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                    ],
                }],
                "max_tokens": 1000,
            }
            req = urllib.request.Request(
                "https://api.openai.com/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
            return VisionResult(text=text, description=text[:200], provider=self.provider)
        except Exception as e:
            return VisionResult(
                text="",
                description=f"[multimodal_llm error] {e}",
                provider=self.provider,
            )


def create_vision(provider: Optional[str] = None) -> BaseVision:
    provider = (provider or os.environ.get("PEAR_VISION_PROVIDER", "auto")).lower()
    if provider == "offline":
        return OfflineVision()
    if provider == "tesseract":
        t = TesseractOCR()
        return t if t.is_available() else OfflineVision()
    if provider in ("multimodal_llm", "llm", "openai"):
        m = MultimodalLLMVision()
        return m if m.is_available() else OfflineVision()
    for cls in (TesseractOCR, MultimodalLLMVision):
        inst = cls()
        if inst.is_available():
            return inst
    return OfflineVision()
