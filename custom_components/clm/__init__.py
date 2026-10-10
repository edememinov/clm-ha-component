"""Native CLM adapter with individual links and private ingress."""

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

import probatio as vol
from homeassistant.core import Context, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.auth.permissions.const import POLICY_CONTROL

from .client import CompanionClient, CompanionError
from .const import DOMAIN, STORAGE_VERSION
from .policy import Authority, Denied


@dataclass
class Runtime:
    client: CompanionClient
    store: Store
    links: dict[str, str] = field(default_factory=dict)
    authority: Authority = field(default_factory=Authority)
    pending: dict = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False


async def linked_user(hass, runtime: Runtime, actor: str) -> str:
    user = await hass.auth.async_get_user(actor)
    if runtime.closed or user is None or not user.is_active or actor not in runtime.links:
        raise Unauthorized()
    return runtime.links[actor]


async def async_setup(hass, config):
    from .websocket import register
    register(hass)

    async def invoke(call):
        runtime = _runtime(hass, call.data["config_entry_id"])
        try:
            actor = runtime.authority.resolve(call.context.user_id, call.context.parent_id, call.data["binding"])
            token = await linked_user(hass, runtime, actor)
            # Personal PINs never enter service data or automation traces.
            result = await runtime.client.request("bindings/invoke", {
                "binding": call.data["binding"], "key": str(uuid4()),
            }, token)
            return {key: result[key] for key in ("id", "status", "code", "duplicate")}
        except Denied:
            raise Unauthorized(context=call.context) from None
        except CompanionError:
            raise HomeAssistantError("CLM request failed.") from None

    hass.services.async_register(DOMAIN, "invoke", invoke, schema=vol.Schema({
        vol.Required("config_entry_id"): str,
        vol.Required("binding"): str,
    }), supports_response=SupportsResponse.OPTIONAL)

    async def run_automation(call):
        runtime = _runtime(hass, call.data["config_entry_id"])
        actor = call.context.user_id
        if not actor:
            raise Unauthorized(context=call.context)
        await linked_user(hass, runtime, actor)
        user = await hass.auth.async_get_user(actor)
        entity = call.data["automation"]
        if not entity.startswith("automation.") or not user.permissions.check_entity(entity, POLICY_CONTROL):
            raise Unauthorized(context=call.context)
        state = hass.states.get(entity)
        if state is None or state.state != "on":
            raise HomeAssistantError("Automation is unavailable or disabled.")
        available = await runtime.client.request("bindings/list", {}, runtime.links[actor])
        allowed = {item["id"] for item in available if not item["personal"] and item["operation"] not in ("external.run", "automation.run")}
        from .bridge import bindings_for
        from homeassistant.components.automation import DATA_COMPONENT
        component = hass.data.get(DATA_COMPONENT)
        automation = component.get_entity(entity) if component else None
        if automation is None:
            raise HomeAssistantError("Automation is unavailable.")
        try:
            bindings = bindings_for(automation.raw_config, call.data["config_entry_id"])
        except ValueError:
            raise HomeAssistantError("Automation binding scope is unsupported.") from None
        if any(binding not in allowed for binding in bindings):
            raise Unauthorized(context=call.context)
        root = Context(user_id=actor)
        runtime.authority.register(root.id, actor, bindings)
        try:
            await hass.services.async_call("automation", "trigger", {
                "entity_id": entity, "skip_condition": False,
            }, blocking=True, context=root)
        finally:
            runtime.authority.remove(root.id)

    hass.services.async_register(DOMAIN, "run_automation", run_automation, schema=vol.Schema({
        vol.Required("config_entry_id"): str,
        vol.Required("automation"): str,
    }))
    return True


def _runtime(hass, entry_id: str) -> Runtime:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not isinstance(entry.runtime_data, Runtime):
        raise HomeAssistantError("CLM connection is unavailable.")
    return entry.runtime_data


async def async_setup_entry(hass, entry):
    store = Store(hass, STORAGE_VERSION, f"clm.{entry.entry_id}.links", private=True)
    data = await store.async_load() or {}
    links = data.get("links", {}) if isinstance(data, dict) else {}
    links = {actor: token for actor, token in links.items() if isinstance(actor, str) and isinstance(token, str) and len(token) == 101} if isinstance(links, dict) else {}
    entry.runtime_data = Runtime(CompanionClient(async_get_clientsession(hass), entry.data["api_origin"]), store, links)
    from .bridge import bridge
    task = entry.async_create_background_task(hass, bridge(hass, entry), "clm companion bridge")
    entry.async_on_unload(task.cancel)
    return True


async def async_unload_entry(hass, entry):
    entry.runtime_data.closed = True
    entry.runtime_data.pending.clear()
    entry.runtime_data.authority = Authority()
    entry.runtime_data.links.clear()
    entry.runtime_data = None
    return True


async def async_remove_entry(hass, entry):
    store = Store(hass, STORAGE_VERSION, f"clm.{entry.entry_id}.links", private=True)
    data = await store.async_load() or {}
    client = CompanionClient(async_get_clientsession(hass), entry.data["api_origin"])
    for token in data.get("links", {}).values():
        try:
            await client.request("revoke", {}, token)
        except CompanionError:
            pass
    await store.async_remove()
