"""
Bind sessions to a client id + browser fingerprint.

Client id: HttpOnly cookie `amica_client_binding_id` only (server-issued).
Not read from headers or WebSocket query — those are forgeable / log-leaky.

Fingerprint (new sessions): User-Agent + Sec-CH-UA-Platform + Sec-CH-UA-Mobile +
Sec-CH-UA-Full-Version-List (stable Client Hints). Verification accepts older variants
(UA-only, legacy Sec-CH-UA block) so existing sessions keep working.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid

from django.conf import settings

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
    return (request.COOKIES.get(CLIENT_BINDING_COOKIE) or "").strip()


def attach_client_binding_cookie_if_needed(request, response) -> str:
    """
    Return the binding id used for this request. If the request has no cookie,
    mint an id and Set-Cookie on the given response (same request cycle).
    """
    cid = (request.COOKIES.get(CLIENT_BINDING_COOKIE) or "").strip()
    if cid:
        return cid
    existing = response.cookies.get(CLIENT_BINDING_COOKIE)
    if existing is not None and existing.value:
        return str(existing.value)
    cid = str(uuid.uuid4())
    secure = getattr(settings, "SESSION_COOKIE_SECURE", False)
    response.set_cookie(
        CLIENT_BINDING_COOKIE,
        cid,
        max_age=63072000,
        httponly=True,
        secure=secure,
        samesite="Lax",
        path="/",
    )
    return cid


def client_device_id_from_scope(scope: dict) -> str:
    headers = _headers_lowercase_from_scope(scope)
    cookies = parse_cookie_header(headers.get("cookie", ""))
    return (cookies.get(CLIENT_BINDING_COOKIE) or "").strip()


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


def ua_only_browser_fingerprint_from_meta(meta: dict) -> str:
    """Stable across GET/POST where Client Hints differ (e.g. Safari)."""
    return meta.get("HTTP_USER_AGENT") or ""


def stable_device_login_challenge_binding_from_scope(scope: dict) -> str:
    """HTTP poll uses cookie + UA; WS login must store the same shape for that device."""
    dev = client_device_id_from_scope(scope)
    headers = _headers_lowercase_from_scope(scope)
    ua = headers.get("user-agent", "")
    return compute_binding_hash(dev, ua)


def poll_binding_matches_device_challenge(request, stored_challenge_hash: str) -> bool:
    """
    Match poll request to DeviceLoginChallenge.new_binding_hash.

    New challenges use UA-only fingerprint (stable). Legacy rows use full enhanced
    fingerprint; accept any variant from the current request (same as session refresh).
    """
    dev = client_device_id_from_request(request)
    if not dev:
        return False
    meta = request.META
    stable = compute_binding_hash(dev, ua_only_browser_fingerprint_from_meta(meta))
    if hmac.compare_digest(stable, stored_challenge_hash):
        return True
    for cand in _binding_candidates_for_device_and_fps(
        dev, _fp_variants_from_meta(meta)
    ):
        if hmac.compare_digest(cand, stored_challenge_hash):
            return True
    return False


def binding_from_request(request, response=None) -> str:
    meta = request.META
    if response is not None:
        device_id = attach_client_binding_cookie_if_needed(request, response)
    else:
        device_id = client_device_id_from_request(request)
    return compute_binding_hash(
        device_id,
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


def ip_and_user_agent_from_scope(scope: dict) -> tuple[str | None, str]:
    """Best-effort client IP and User-Agent for WebSocket logins (proxy headers)."""
    headers = _headers_lowercase_from_scope(scope)
    raw_ip = (
        (headers.get("cf-connecting-ip") or "").strip()
        or (headers.get("x-forwarded-for") or "").split(",")[0].strip()
        or (headers.get("x-real-ip") or "").strip()
        or ""
    )
    ua = headers.get("user-agent", "") or ""
    return (raw_ip or None, ua)


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
    return _binding_candidates_for_device_and_fps(dev, fps)


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


def client_device_id_from_meta(meta: dict) -> str:
    """Cookie-only device id from a WSGI-style META dict (needs HTTP_COOKIE)."""
    cookies = parse_cookie_header(meta.get("HTTP_COOKIE", ""))
    return (cookies.get(CLIENT_BINDING_COOKIE) or "").strip()
