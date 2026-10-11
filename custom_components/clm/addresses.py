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


def browser_connection(data, audience=None, mode="auto"):
    """Select only stored addresses and require an exact approved HA origin."""
    allowed = data.get("ha_origins", [data["ha_origin"]])
    audience = web_origin(audience or data["ha_origin"], allow_http=True)
    if audience not in allowed or mode not in ("auto", "local", "online"):
        raise ValueError("Unapproved browser address.")
    local = data.get("workspace_local_origin")
    use_local = mode == "local" or (mode == "auto" and audience == data.get("internal_ha_origin"))
    if mode == "local" and not local:
        raise ValueError("No local CLM address configured.")
    workspace = local if use_local and local else data["workspace_origin"]
    if audience.startswith("https:") and workspace.startswith("http:"):
        if mode == "auto" and data["workspace_origin"].startswith("https:"):
            workspace = data["workspace_origin"]
        else:
            raise ValueError("HTTPS dashboards require an HTTPS workspace.")
    return audience, workspace
