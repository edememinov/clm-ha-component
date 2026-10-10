"""Private HA connection APIs; payloads cannot nominate an actor."""

from time import monotonic
import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError, Unauthorized

from .client import CompanionError


def register(hass):
    websocket_api.async_register_command(hass, handle)


@websocket_api.websocket_command({
    vol.Required("type"): "clm/request",
    vol.Required("config_entry_id"): str,
    vol.Required("operation"): vol.In(["status", "pair", "exchange", "view", "bindings", "invoke", "revoke"]),
    vol.Optional("nonce"): str,
    vol.Optional("binding"): str,
})
@websocket_api.async_response
async def handle(hass, connection, msg):
    from . import _runtime, linked_user
    try:
        runtime = _runtime(hass, msg["config_entry_id"])
        if runtime.closed:
            raise Unauthorized()
        actor = connection.user.id
        user = await hass.auth.async_get_user(actor)
        if user is None or not user.is_active:
            raise Unauthorized()
        entry = hass.config_entries.async_get_entry(msg["config_entry_id"])
        operation = msg["operation"]
        if operation == "status":
            linked = actor in runtime.links
            if linked:
                try:
                    await runtime.client.request("capabilities", {}, runtime.links[actor])
                except CompanionError:
                    linked = False
            result = {"linked": linked, "workspace_origin": entry.data["workspace_origin"], "version": 1}
        elif operation == "pair":
            async with runtime.lock:
                runtime.pending = {key: value for key, value in runtime.pending.items() if value["expires_local"] > monotonic()}
                if len(runtime.pending) >= 100:
                    raise HomeAssistantError("Too many pending requests.")
                result = await runtime.client.request("challenges", {"actor": actor}, entry.data["auth_token"])
                if runtime.closed:
                    raise Unauthorized()
                runtime.pending[actor] = {**result, "expires_local": monotonic() + 600}
                result = {"approval_url": f"{entry.data['workspace_origin']}/companions/pair/{result['installation']}/{result['challenge']}"}
        elif operation == "exchange":
            async with runtime.lock:
                pending = runtime.pending.get(actor)
                if pending is None or pending["expires_local"] <= monotonic():
                    raise Unauthorized()
                result = await runtime.client.request("challenges/exchange", {
                    "challenge": pending["challenge"], "pollToken": pending["pollToken"],
                }, entry.data["auth_token"])
                if result.get("approved"):
                    if runtime.closed:
                        raise Unauthorized()
                    runtime.links[actor] = result["credential"]
                    await runtime.store.async_save({"links": runtime.links})
                    runtime.pending.pop(actor, None)
                result = {"approved": bool(result.get("approved"))}
        else:
            token = await linked_user(hass, runtime, actor)
            if operation == "view":
                result = await runtime.client.request("ui-sessions", {"nonce": msg.get("nonce", ""), "audience": entry.data["ha_origin"]}, token)
                result["workspace_origin"] = entry.data["workspace_origin"]
            elif operation == "bindings":
                result = await runtime.client.request("bindings/list", {}, token)
            elif operation == "invoke":
                from uuid import uuid4
                result = await runtime.client.request("bindings/invoke", {"binding": msg.get("binding", ""), "key": str(uuid4())}, token)
            else:
                await runtime.client.request("revoke", {}, token)
                async with runtime.lock:
                    runtime.links.pop(actor, None)
                    await runtime.store.async_save({"links": runtime.links})
                result = {"revoked": True}
        connection.send_result(msg["id"], result)
    except Unauthorized:
        connection.send_error(msg["id"], "unauthorized", "CLM request denied.")
    except (CompanionError, HomeAssistantError, KeyError):
        connection.send_error(msg["id"], "clm_unavailable", "CLM request unavailable.")
