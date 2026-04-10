"""Shared active-session display data for REST and WebSocket (GeoIP + UA parsing)."""

import json
import re

from django.contrib.gis.geoip2 import GeoIP2
from django.utils.crypto import constant_time_compare

from apps.accounts.models import ActiveSession


def _parse_device_without_versions(ua: str) -> str:
    """Device-trust login UI / emails: browser + OS names only (no version numbers)."""
    browser = "Other"
    browser_patterns = [
        ("Chrome", r"CriOS/[\d\.]+"),
        ("Chrome", r"Chrome/[\d\.]+"),
        ("Firefox", r"FxiOS/[\d\.]+"),
        ("Firefox", r"Firefox/[\d\.]+"),
        ("Edge", r"Edg/[\d\.]+"),
        ("Opera", r"OPR/[\d\.]+"),
        ("Safari", r"Version/[\d\.]+.*Safari"),
    ]
    for name, pattern in browser_patterns:
        if re.search(pattern, ua):
            browser = name
            break
    if browser == "Other" and re.search(r"Chromium/[\d\.]+", ua):
        browser = "Chrome"

    os_name = "Other"
    if "Android" in ua:
        os_name = "Android"
    elif "iPad" in ua and (
        "iPhone OS" in ua or "CPU OS" in ua or "like Mac OS X" in ua
    ):
        os_name = "iPad"
    elif "iPhone" in ua or "iPod" in ua or "iPhone OS" in ua:
        os_name = "iPhone"
    elif "Windows NT" in ua:
        os_name = "Windows"
    elif "Mac OS X" in ua:
        os_name = "Mac"
    elif "Linux" in ua:
        os_name = "Linux"

    return f"{browser} on {os_name}"


def _normalize_version(version: str) -> str:
    raw = (version or "").strip()
    if not raw:
        return ""
    parts = raw.split(".")
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def _ua_hints_dict(raw: str | None) -> dict[str, str]:
    payload = (raw or "").strip()
    if not payload:
        return {}
    try:
        data = json.loads(payload)
        if not isinstance(data, dict):
            return {}
        return {
            str(k): str(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, str)
        }
    except Exception:
        return {}


def _browser_version_from_hints(browser_name: str, hints: dict[str, str]) -> str:
    full = hints.get("sec_ch_ua_full_version_list", "")
    if not full:
        return ""
    pairs = re.findall(r'"([^"]+)";\s*v="([^"]+)"', full)
    if not pairs:
        return ""
    preferred_brands = {
        "Chrome": ("Google Chrome", "Chromium", "Chrome"),
        "Edge": ("Microsoft Edge", "Edge", "Chromium"),
        "Opera": ("Opera", "OPR", "Chromium"),
        "Firefox": ("Firefox",),
        "Safari": ("Safari",),
    }.get(browser_name, ())
    brand_to_version = {brand: _normalize_version(version) for brand, version in pairs}
    for brand in preferred_brands:
        version = brand_to_version.get(brand, "")
        if version:
            return version
    return ""


def _browser_name_and_version_from_ua(ua: str) -> tuple[str, str]:
    browser = "Other"
    browser_version = ""
    browser_patterns = [
        ("Chrome", r"CriOS/([\d\.]+)"),
        ("Chrome", r"Chrome/([\d\.]+)"),
        ("Firefox", r"FxiOS/([\d\.]+)"),
        ("Firefox", r"Firefox/([\d\.]+)"),
        ("Safari", r"Version/([\d\.]+).*Safari"),
        ("Edge", r"Edg/([\d\.]+)"),
        ("Opera", r"OPR/([\d\.]+)"),
    ]
    for name, pattern in browser_patterns:
        match = re.search(pattern, ua)
        if match:
            browser = name
            browser_version = _normalize_version(match.group(1))
            break
    if browser == "Other":
        m = re.search(r"Chromium/([\d\.]+)", ua)
        if m:
            browser = "Chrome"
            browser_version = _normalize_version(m.group(1))
    return browser, browser_version


def _device_with_hints_versions(ua: str, ua_hints_raw: str | None) -> str:
    browser, browser_version = _browser_name_and_version_from_ua(ua)

    os_name = "Other"
    os_patterns = [
        ("Windows", r"Windows NT [\d\.]+"),
        ("Mac", r"Mac OS X [\d_]+"),
        ("Linux", r"Linux"),
        ("iOS", r"iPhone OS [\d_]+"),
        ("Android", r"Android [\d\.]+"),
    ]
    for name, pattern in os_patterns:
        if re.search(pattern, ua):
            os_name = name
            break

    hints = _ua_hints_dict(ua_hints_raw)
    # Browser version from UA is acceptable; only platform version is hint-gated.
    if not browser_version:
        browser_version = _browser_version_from_hints(browser, hints)
    platform_version = _normalize_version(
        hints.get("sec_ch_ua_platform_version", "")
    )

    os_str = os_name
    os_version = platform_version
    if os_name == "iOS":
        if "iPad" in ua:
            os_str = f"iPad {os_version}".strip() if os_version else "iPad"
        elif "iPhone" in ua or "iPod" in ua:
            os_str = f"iPhone {os_version}".strip() if os_version else "iPhone"
    elif os_version:
        os_str = f"{os_name} {os_version}"
    browser_str = f"{browser} {browser_version}" if browser_version else browser

    return f"{browser_str} on {os_str}"


def parse_device_from_user_agent(
    user_agent: str,
    *,
    include_versions: bool = True,
    user_agent_hints: str | None = None,
) -> str:
    """
    Human-readable "Browser on OS" from User-Agent.

    - ``include_versions=True`` (default): browser version may come from
      User-Agent; platform version is included only from trusted Client Hints.
    - ``include_versions=False``: minimal client-visible labels only.
    """
    ua = user_agent or ""
    if not include_versions:
        return _parse_device_without_versions(ua)
    return _device_with_hints_versions(ua, user_agent_hints)


def city_country_for_ip(
    ip,
    geo: GeoIP2,
    ip_cache: dict,
):
    """One GeoIP2.city() per distinct IP (cached on this dict)."""
    if not ip:
        return None, None
    if ip in ip_cache:
        return ip_cache[ip]
    try:
        info = geo.city(ip)
        result = (info.get("city"), info.get("country_name"))
    except Exception:
        result = (None, None)
    ip_cache[ip] = result
    return result


def active_session_model_to_dict(
    instance: ActiveSession,
    *,
    current_jti,
    geo,
    ip_cache,
    trusted_binding_hash: str | None = None,
):
    city, country = city_country_for_ip(instance.ip_address, geo, ip_cache)
    tb = (trusted_binding_hash or "").strip()
    bh = (instance.binding_hash or "").strip()
    is_trusted = True if trusted_binding_hash is None else bool(
        tb and bh and constant_time_compare(bh, tb)
    )
    return {
        "jti": instance.jti,
        "ip_address": instance.ip_address or "",
        "user_agent": instance.user_agent or "",
        "created_at": instance.created_at.isoformat(),
        "expires_at": instance.expires_at.isoformat(),
        "last_active": instance.last_active.isoformat(),
        "is_current": bool(current_jti and instance.jti == current_jti),
        "device": parse_device_from_user_agent(
            instance.user_agent or "",
            user_agent_hints=instance.user_agent_hints,
        ),
        "city": city,
        "country": country,
        "is_trusted": is_trusted,
    }


def trusted_device_minimal_label(user) -> str:
    """
    Human-readable device label (browser on OS, no versions) from the user's
    most recently active session.
    Empty if unknown (e.g. no active session with User-Agent).
    """
    for sess in ActiveSession.objects.filter(user=user).order_by("-last_active")[:8]:
        ua = (sess.user_agent or "").strip()
        if ua:
            return parse_device_from_user_agent(ua, include_versions=False)
    return ""


def device_login_notify_extras(
    request_ip,
    request_user_agent: str | None,
    *,
    include_versions: bool = True,
) -> dict[str, str]:
    """
    GeoIP city/country + parsed device string for trusted-device login alerts
    (same GeoIP path as active sessions).

    ``include_versions=True``: WebSocket to trusted clients and security email
    (browser/OS versions like active sessions).

    ``include_versions=False``: only if some client-visible JSON must stay
    minimal (no versions).
    """
    city, country = None, None
    if request_ip:
        try:
            geo = GeoIP2()
            city, country = city_country_for_ip(request_ip, geo, {})
        except Exception:
            pass
    device = parse_device_from_user_agent(
        request_user_agent or "", include_versions=include_versions
    )
    return {
        "request_city": city or "",
        "request_country": country or "",
        "request_device": device,
    }


def serialize_active_sessions_for_ws_user(user, current_jti):
    """All sessions for user; one GeoIP2 reader and one lookup per distinct IP."""
    geo = GeoIP2()
    ip_cache = {}
    sessions = ActiveSession.objects.filter(user=user).order_by("-created_at")
    return [
        active_session_model_to_dict(
            s,
            current_jti=current_jti,
            geo=geo,
            ip_cache=ip_cache,
            trusted_binding_hash=None,
        )
        for s in sessions
    ]
