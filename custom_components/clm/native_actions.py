"""Direct HA-owned automation actions and native worker delivery."""

from uuid import uuid4
import probatio as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .client import CompanionError
from .const import DOMAIN


async def action_account(hass, runtime, context, requested=None):
    """Interactive users retain their own link; unattended calls never pick among owners."""
    from . import linked_user
    actor = context.user_id
    if actor:
        if requested and requested != actor:
            raise Unauthorized(context=context)
    elif requested:
        actor = requested
    elif len(runtime.links) == 1:
        actor = next(iter(runtime.links))
    else:
        raise HomeAssistantError("Select a linked HA account when this connection has multiple accounts.")
    return await linked_user(hass, runtime, actor)


def register(hass):
    base = {
        vol.Required("config_entry_id"): str,
        vol.Optional("account_user_id"): str,
    }

    async def devices(call):
        from . import _runtime
        runtime = _runtime(hass, call.data["config_entry_id"])
        token = await action_account(hass, runtime, call.context, call.data.get("account_user_id"))
        try:
            rows = await runtime.client.request("native/devices", {}, token)
            return {"devices": [{key: row[key] for key in ("id", "name", "online", "version")} for row in rows]}
        except CompanionError:
            raise HomeAssistantError("CLM device status is unavailable. Update the backend and Windows worker.") from None

    hass.services.async_register(DOMAIN, "list_devices", devices, schema=vol.Schema(base), supports_response=SupportsResponse.ONLY)

    async def execute(call, operation):
        from . import _runtime
        runtime = _runtime(hass, call.data["config_entry_id"])
        token = await action_account(hass, runtime, call.context, call.data.get("account_user_id"))
        payload = {
            "operation": operation, "device": call.data["device"], "pin": call.data["pin"],
            "name": call.data.get("preset_name", call.data.get("session_name", "")),
            "match": call.data.get("match", "latest"), "incognito": call.data.get("incognito", False),
            "key": str(uuid4()),
        }
        try:
            result = await runtime.client.request("native/actions", payload, token)
            if result.get("status") == "failed":
                raise HomeAssistantError("CLM native launch failed.")
            return {key: result[key] for key in ("id", "status", "duplicate") if key in result}
        except CompanionError:
            raise HomeAssistantError("CLM action failed. Check the linked account, PIN, unique preset name and online Windows worker.") from None
        finally:
            payload.pop("pin", None)

    common = {**base, vol.Required("device"): str, vol.Required("pin"): str, vol.Optional("incognito", default=False): bool}
    for service, operation, fields in (
        ("start_session", "start_session", {vol.Required("preset_name"): str}),
        ("open_selkies", "open_selkies", {vol.Required("session_name"): str, vol.Optional("match", default="latest"): vol.In(["all", "latest"])}),
        ("open_clm", "open_clm", {}),
    ):
        async def handler(call, operation=operation):
            return await execute(call, operation)
        hass.services.async_register(DOMAIN, service, handler, schema=vol.Schema({**common, **fields}), supports_response=SupportsResponse.OPTIONAL)
