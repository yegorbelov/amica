"""Shared active-session display data for REST and WebSocket (GeoIP + UA parsing)."""

import re

from django.contrib.gis.geoip2 import GeoIP2

from apps.accounts.models import ActiveSession


def parse_device_from_user_agent(user_agent: str) -> str:
    ua = user_agent or ""

    browser = "Other"
    browser_version = ""
    browser_patterns = [
        ("Chrome", r"Chrome/([\d\.]+)"),
        ("Firefox", r"Firefox/([\d\.]+)"),
        ("Safari", r"Version/([\d\.]+).*Safari"),
        ("Edge", r"Edg/([\d\.]+)"),
        ("Opera", r"OPR/([\d\.]+)"),
    ]

    for name, pattern in browser_patterns:
        match = re.search(pattern, ua)
        if match:
            browser = name
            browser_version = match.group(1)
            break

    if browser_version:
        parts = browser_version.split(".")
        while len(parts) > 1 and parts[-1] == "0":
            parts.pop()
        browser_version = ".".join(parts)

    os_name = "Other"
    os_version = ""
    os_patterns = [
        ("Windows", r"Windows NT ([\d\.]+)"),
        ("Mac", r"Mac OS X ([\d_]+)"),
        ("Linux", r"Linux"),
        ("iOS", r"iPhone OS ([\d_]+)"),
        ("Android", r"Android ([\d\.]+)"),
    ]

    for name, pattern in os_patterns:
        match = re.search(pattern, ua)
        if match:
            os_name = name
            if match.groups():
                os_version = match.group(1).replace("_", ".")
            break

    os_str = f"{os_name} {os_version}" if os_version else os_name
    browser_str = f"{browser} {browser_version}" if browser_version else browser

    return f"{browser_str} on {os_str}"


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


def active_session_model_to_dict(instance: ActiveSession, *, current_jti, geo, ip_cache):
    city, country = city_country_for_ip(instance.ip_address, geo, ip_cache)
    return {
        "jti": instance.jti,
        "ip_address": instance.ip_address or "",
        "user_agent": instance.user_agent or "",
        "created_at": instance.created_at.isoformat(),
        "expires_at": instance.expires_at.isoformat(),
        "last_active": instance.last_active.isoformat(),
        "is_current": bool(current_jti and instance.jti == current_jti),
        "device": parse_device_from_user_agent(instance.user_agent or ""),
        "city": city,
        "country": country,
    }


def serialize_active_sessions_for_ws_user(user, current_jti):
    """All sessions for user; one GeoIP2 reader and one lookup per distinct IP."""
    geo = GeoIP2()
    ip_cache = {}
    sessions = ActiveSession.objects.filter(user=user).order_by("-created_at")
    return [
        active_session_model_to_dict(s, current_jti=current_jti, geo=geo, ip_cache=ip_cache)
        for s in sessions
    ]
