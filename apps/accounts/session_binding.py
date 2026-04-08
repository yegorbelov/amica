"""
Bind sessions to a client id + browser fingerprint.

Client id priority: HttpOnly cookie `amica_client_binding_id` (server-issued), then
X-Client-Binding header, then WebSocket query `client_binding`.

Fingerprint (new sessions): User-Agent + Sec-CH-UA-Platform + Sec-CH-UA-Mobile +
Sec-CH-UA-Full-Version-List (stable Client Hints). Verification accepts older variants
(UA-only, legacy Sec-CH-UA block) so existing sessions keep working.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import parse_qs, unquote

from django.conf import settings

BINDING_HEADER_META = "HTTP_X_CLIENT_BINDING"
JWT_BINDING_CLAIM = "session_bdg"
CLIENT_BINDING_COOKIE = "amica_client_binding_id"


def _signing_key() -> bytes:
    sk = settings.SECRET_KEY
    if isinstance(sk, str):
        return sk.encode()
    return bytes(sk)


def parse_cookie_header(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if not raw:
        return out
    for segment in raw.split(";"):
        segment = segment.strip()
        if "=" in segment:
            k, v = segment.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def client_device_id_from_request(request) -> str:
    cid = (request.COOKIES.get(CLIENT_BINDING_COOKIE) or "").strip()
    if cid:
        return cid
    return (request.META.get(BINDING_HEADER_META) or "").strip()


def client_device_id_from_scope(scope: dict) -> str:
    headers = _headers_lowercase_from_scope(scope)
    cookies = parse_cookie_header(headers.get("cookie", ""))
    cid = (cookies.get(CLIENT_BINDING_COOKIE) or "").strip()
    if cid:
        return cid
    device = (headers.get("x-client-binding") or "").strip()
    if device:
        return device
    return client_binding_from_query(scope)


def enhanced_browser_fingerprint_from_meta(meta: dict) -> str:
    parts = [
        meta.get("HTTP_USER_AGENT") or "",
        meta.get("HTTP_SEC_CH_UA_PLATFORM") or "",
        meta.get("HTTP_SEC_CH_UA_MOBILE") or "",
        meta.get("HTTP_SEC_CH_UA_FULL_VERSION_LIST") or "",
    ]
    return "\n".join(parts)


def _legacy_sec_ch_fp_from_meta(meta: dict) -> str:
    ua = meta.get("HTTP_USER_AGENT") or ""
    sec_ch = meta.get("HTTP_SEC_CH_UA") or ""
    platform = meta.get("HTTP_SEC_CH_UA_PLATFORM") or ""
    mobile = meta.get("HTTP_SEC_CH_UA_MOBILE") or ""
    return f"{ua}\n{sec_ch}\n{platform}\n{mobile}"


def _fp_variants_from_meta(meta: dict) -> set[str]:
    return {
        enhanced_browser_fingerprint_from_meta(meta),
        meta.get("HTTP_USER_AGENT") or "",
        _legacy_sec_ch_fp_from_meta(meta),
    }


def compute_binding_hash(device_id: str, browser_fp: str) -> str:
    msg = f"{device_id}\n{browser_fp}".encode("utf-8")
    return hmac.new(_signing_key(), msg, hashlib.sha256).hexdigest()


def binding_from_request(request) -> str:
    meta = request.META
    return compute_binding_hash(
        client_device_id_from_request(request),
        enhanced_browser_fingerprint_from_meta(meta),
    )


def _headers_lowercase_from_scope(scope: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in scope.get("headers", []):
        if isinstance(k, bytes):
            k = k.decode("latin1")
        if isinstance(v, bytes):
            v = v.decode("latin1")
        out[k.lower()] = v
    return out


def client_binding_from_query(scope: dict) -> str:
    raw = scope.get("query_string", b"") or b""
    if not raw:
        return ""
    qs = parse_qs(raw.decode(), keep_blank_values=True)
    vals = qs.get("client_binding") or []
    if not vals:
        return ""
    return unquote(vals[0]).strip()


def _fp_variants_from_scope(scope: dict) -> set[str]:
    headers = _headers_lowercase_from_scope(scope)
    meta_like = {
        "HTTP_USER_AGENT": headers.get("user-agent", ""),
        "HTTP_SEC_CH_UA_PLATFORM": headers.get("sec-ch-ua-platform", ""),
        "HTTP_SEC_CH_UA_MOBILE": headers.get("sec-ch-ua-mobile", ""),
        "HTTP_SEC_CH_UA_FULL_VERSION_LIST": headers.get(
            "sec-ch-ua-full-version-list", ""
        ),
        "HTTP_SEC_CH_UA": headers.get("sec-ch-ua", ""),
    }
    return _fp_variants_from_meta(meta_like)


def binding_from_scope(scope: dict) -> str:
    device = client_device_id_from_scope(scope)
    headers = _headers_lowercase_from_scope(scope)
    meta_like = {
        "HTTP_USER_AGENT": headers.get("user-agent", ""),
        "HTTP_SEC_CH_UA_PLATFORM": headers.get("sec-ch-ua-platform", ""),
        "HTTP_SEC_CH_UA_MOBILE": headers.get("sec-ch-ua-mobile", ""),
        "HTTP_SEC_CH_UA_FULL_VERSION_LIST": headers.get(
            "sec-ch-ua-full-version-list", ""
        ),
        "HTTP_SEC_CH_UA": headers.get("sec-ch-ua", ""),
    }
    return compute_binding_hash(
        device,
        enhanced_browser_fingerprint_from_meta(meta_like),
    )


def _binding_candidates_for_device_and_fps(device: str, fps: set[str]) -> set[str]:
    return {compute_binding_hash(device, fp) for fp in fps}


def _binding_candidates_from_request(request) -> set[str]:
    dev = client_device_id_from_request(request)
    fps = _fp_variants_from_meta(request.META)
    cands = _binding_candidates_for_device_and_fps(dev, fps)
    header_dev = (request.META.get(BINDING_HEADER_META) or "").strip()
    if header_dev and header_dev != dev:
        cands |= _binding_candidates_for_device_and_fps(header_dev, fps)
    return cands


def _binding_candidates_from_scope(scope: dict) -> set[str]:
    dev = client_device_id_from_scope(scope)
    fps = _fp_variants_from_scope(scope)
    return _binding_candidates_for_device_and_fps(dev, fps)


def session_binding_matches_session(session, *, request=None, scope: dict | None = None) -> bool:
    if not session.binding_hash:
        return True
    if request is not None:
        return session.binding_hash in _binding_candidates_from_request(request)
    if scope is not None:
        return session.binding_hash in _binding_candidates_from_scope(scope)
    return False


def browser_fingerprint_from_meta(meta: dict) -> str:
    return enhanced_browser_fingerprint_from_meta(meta)


# Backwards compat for imports
def client_device_id_from_meta(meta: dict) -> str:
    return (meta.get(BINDING_HEADER_META) or "").strip()
