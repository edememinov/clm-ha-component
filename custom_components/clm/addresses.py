"""Browser addresses are distinct from the server-to-server transport address."""

from .client import web_origin


def discover_origins(hass):
    """Use HA's configured internal, external and cloud URLs, never caller input."""
    from homeassistant.helpers.network import get_url, NoURLAvailableError
    origins = []
    internal = None
    for options in (
        {"allow_external": False, "allow_cloud": False},
        {"allow_internal": False, "allow_cloud": False},
        {"require_cloud": True},
    ):
        try:
            value = web_origin(get_url(hass, **options), allow_http=True)
        except (NoURLAvailableError, ValueError):
            continue
        if not options.get("allow_external", True):
            internal = value
        if value not in origins:
            origins.append(value)
    return origins, internal


def workspace_origins(data, audience, mode="auto"):
    """Offer ordered, explicitly configured browser URLs for this access mode."""
    local = data.get("workspace_local_origin")
    fallback = data.get("workspace_local_fallback_origin")
    use_local = mode in ("auto", "local")
    if mode == "local" and not (local or fallback):
        raise ValueError("No local CLM address configured.")
    candidates = [local, fallback] if use_local else []
    if mode != "local":
        candidates.append(data["workspace_origin"])
    candidates = list(dict.fromkeys(value for value in candidates if value))
    if audience.startswith("https:"):
        candidates = [value for value in candidates if value.startswith("https:")]
    if not candidates:
        raise ValueError("HTTPS dashboards require an HTTPS workspace.")
    return candidates


def browser_connection(data, audience=None, mode="auto", workspace=None):
    """Select only stored addresses and require an exact approved HA origin."""
    allowed = data.get("ha_origins", [data["ha_origin"]])
    audience = web_origin(audience or data["ha_origin"], allow_http=True)
    if audience not in allowed or mode not in ("auto", "local", "online"):
        raise ValueError("Unapproved browser address.")
    candidates = workspace_origins(data, audience, mode)
    selected = web_origin(workspace, allow_http=True) if workspace else candidates[0]
    if selected not in candidates:
        raise ValueError("Unapproved CLM web address.")
    return audience, selected
