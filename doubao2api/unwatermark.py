"""Resolve the watermark-free URL of a Doubao-generated video.

The creation block's download_url is stamped (lr=video_gen_watermark_dyn), but
it also carries a video_model payload holding a fallback_api. Re-querying that
API with logo_type=unwatermarked returns a clean, higher-bitrate rendition —
whose main_url is AES-CBC encrypted under a key derived from the payload's
key_seed rather than plain base64.

Ported from ai-media-extractor (MIT, Copyright (c) 2026 Hmily).
"""

import base64
import hashlib
import json
import logging
import re
import urllib.parse
from typing import Any, Dict, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

QAAB_SALT = bytes.fromhex(
    "4dd4c2e6b83162090e52b3c7a6733ba41cb2462b829ab58a196b39db57177524"
    "f49baf7f08e8d68d26a72e37c1a95a2f1f05a51892aef2949732b62a38aadd58"
)

_HEADERS = {
    "accept": "application/json,text/plain,*/*",
    "origin": "https://www.doubao.com",
    "referer": "https://www.doubao.com/",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


def _with_no_watermark_options(url: str) -> str:
    """Rewrite the fallback_api query to ask for the unstamped rendition."""
    parsed = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    options = {"channel": "no", "codec_type": "8", "logo_type": "unwatermarked"}
    query = [(key, options.pop(key, value)) for key, value in query]
    query.extend(options.items())
    return urllib.parse.urlunsplit(
        parsed._replace(query=urllib.parse.urlencode(query))
    )


def _b64_decode_loose(value: str) -> Optional[bytes]:
    raw = str(value or "").strip()
    candidate = raw.replace("-", "+").replace("_", "/")
    try:
        return base64.b64decode(candidate + "=" * (-len(candidate) % 4))
    except (ValueError, UnicodeEncodeError):
        return None


def _url_from_bytes(value: bytes) -> str:
    try:
        text = value.decode("ascii").strip()
    except UnicodeDecodeError:
        return ""
    # Decrypted payloads keep PKCS padding after the signed query string, and a
    # URL cannot contain ASCII control bytes.
    text = re.split(r"[\x00-\x1f\x7f]", text, maxsplit=1)[0]
    return text if text.startswith(("https://", "http://")) else ""


def _strip_pkcs7(value: bytes) -> bytes:
    if not value:
        return value
    pad = value[-1]
    if 0 < pad <= len(value) and value[-pad:] == bytes([pad]) * pad:
        return value[:-pad]
    return value


def _decrypt_main_url(token: str, key_seed: str) -> str:
    """Decrypt an AES-CBC main_url token; '' when it cannot be recovered."""
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher,
            algorithms,
            modes,
        )
    except ImportError:
        log.warning("unwatermark: cryptography not installed, "
                    "cannot decrypt main_url")
        return ""

    token_bytes = _b64_decode_loose(token)
    seed_bytes = _b64_decode_loose(key_seed)
    if not token_bytes or not seed_bytes:
        return ""
    derived = hashlib.sha512(
        hashlib.sha512(seed_bytes[:32]).digest() + QAAB_SALT
    ).digest()
    key_a, key_b = derived[:16], derived[16:32]

    attempts = []
    if token_bytes.startswith(b"\xa8\x00\x01\x00"):
        attempts += [(token_bytes[4:], key_a, key_b),
                     (token_bytes[4:], key_b, key_a)]
        if len(token_bytes) > 36:
            attempts += [(token_bytes[36:], key_a, token_bytes[20:36]),
                         (token_bytes[36:], key_a, key_b)]
    else:
        attempts.append((token_bytes, key_a, key_b))

    for payload, key, iv in attempts:
        if not payload or len(payload) % 16:
            continue
        try:
            plain = Cipher(
                algorithms.AES(key), modes.CBC(iv)
            ).decryptor().update(payload)
        except ValueError:
            continue
        url = _url_from_bytes(plain) or _url_from_bytes(_strip_pkcs7(plain))
        if url:
            return url
    return ""


def _find_key_seed(value: Any, depth: int = 0) -> str:
    """Locate a key_seed anywhere in the response, nested or inside a URL."""
    if depth > 10 or value is None:
        return ""
    if isinstance(value, str):
        match = re.search(r'(?:^|[?&])key_seed=([^&"\'<>\\\s]+)', value)
        return urllib.parse.unquote(match.group(1)) if match else ""
    if isinstance(value, dict):
        direct = value.get("key_seed")
        if isinstance(direct, str) and direct:
            return direct
        children = list(value.values())
    elif isinstance(value, list):
        children = value
    else:
        return ""
    for child in children:
        found = _find_key_seed(child, depth + 1)
        if found:
            return found
    return ""


def _best_rendition(payload: Any, key_seed: str) -> Tuple[str, Dict[str, Any]]:
    """Pick the highest-quality entry from video_list and resolve its URL."""
    root = payload.get("video_info") or payload
    data = root.get("data", root) if isinstance(root, dict) else {}
    video_list = data.get("video_list") if isinstance(data, dict) else None
    items = (
        [v for v in video_list.values() if isinstance(v, dict)]
        if isinstance(video_list, dict) else []
    )

    best = None
    for item in items:
        token = item.get("main_url") or item.get("play_url") or ""
        if not isinstance(token, str) or not token.strip():
            continue
        score = (
            int(item.get("bitrate") or item.get("real_bitrate") or 0)
            + int(item.get("vwidth") or 0) * int(item.get("vheight") or 0)
        )
        if best is None or score > best[0]:
            best = (score, token.strip(), item)
    if best is None:
        return "", {}

    _, token, item = best
    if token.startswith(("https://", "http://")):
        return token, item
    plain = _b64_decode_loose(token)
    url = _url_from_bytes(plain or b"")
    return url or _decrypt_main_url(token, key_seed), item


async def resolve_unwatermarked(
    video_model_raw: str, timeout: float = 30
) -> Dict[str, Any]:
    """Return {url, width, height, definition} for the clean rendition.

    Returns an empty dict when the video carries no fallback_api or the
    unwatermarked rendition cannot be resolved; callers keep the stamped
    download_url in that case.
    """
    if not video_model_raw:
        return {}
    try:
        video_model = json.loads(video_model_raw)
    except (json.JSONDecodeError, TypeError):
        return {}

    fallback_api = video_model.get("fallback_api")
    if not fallback_api:
        return {}

    # A fresh client: this is a third-party host, so no Doubao session cookies.
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                _with_no_watermark_options(fallback_api), headers=_HEADERS
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        log.warning("unwatermark: fallback_api failed: %s", exc)
        return {}

    key_seed = _find_key_seed(payload) or video_model.get("key_seed", "")
    url, meta = _best_rendition(payload, key_seed)
    if not url:
        log.warning("unwatermark: could not resolve a clean main_url")
        return {}
    return {
        "url": url,
        "width": meta.get("vwidth") or 0,
        "height": meta.get("vheight") or 0,
        "definition": meta.get("definition") or "",
    }
