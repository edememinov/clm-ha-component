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
    # Entities allowed per linked account (from the connection's subentries), and grants just allowed.
    grants: dict[str, set[str]] = field(default_factory=dict)
    fresh: dict[str, float] = field(default_factory=dict)
    # Requests already announced with clm_access_requested since starting.
    asked: set[str] = field(default_factory=set)
    # CLM items each linked account may run, for the Run CLM item action's pickers.
    items: dict[str, list[dict]] = field(default_factory=dict)
    operations: dict[str, str] = field(default_factory=dict)
    grant_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    # Per account: the offer's fingerprint and each state's signature as last sent, and the entities CLM's workflows use.
    exchange: dict = field(default_factory=dict)


async def linked_user(hass, runtime: Runtime, actor: str) -> str:
    user = await hass.auth.async_get_user(actor)
    if runtime.closed or user is None or not user.is_active or actor not in runtime.links:
        raise Unauthorized()
    return runtime.links[actor]


async def async_setup(hass, config):
    from .websocket import register
    register(hass)
    from .native_actions import register as register_native_actions
    register_native_actions(hass)
    from .items import register as register_items
    register_items(hass)

    async def invoke(call):
        runtime = _runtime(hass, call.data["config_entry_id"])
        return await execute_binding(hass, runtime, call.context, call.data["binding"])

    hass.services.async_register(DOMAIN, "invoke", invoke, schema=vol.Schema({
        vol.Required("config_entry_id"): str,
        vol.Required("binding"): str,
    }), supports_response=SupportsResponse.OPTIONAL)

    async def list_bindings(call):
        runtime = _runtime(hass, call.data["config_entry_id"])
        if not call.context.user_id:
            raise Unauthorized(context=call.context)
        token = await linked_user(hass, runtime, call.context.user_id)
        try:
            bindings = await runtime.client.request("bindings/list", {}, token)
            return {"bindings": [
                {"id": item["id"], "operation": item["operation"], "expires": item["expires"], "pending": bool(item.get("pending"))}
                for item in bindings if not item["personal"]
                and (not call.data.get("workflows_only", True) or item["operation"] == "automation.run")
            ]}
        except CompanionError:
            raise HomeAssistantError("CLM bindings are unavailable.") from None

    hass.services.async_register(DOMAIN, "list_bindings", list_bindings, schema=vol.Schema({
        vol.Required("config_entry_id"): str,
        vol.Optional("workflows_only", default=True): bool,
    }), supports_response=SupportsResponse.ONLY)

    async def run_workflow(call):
        runtime = _runtime(hass, call.data["config_entry_id"])
        result = await execute_binding(hass, runtime, call.context, call.data["binding"], "automation.run")
        if result["status"] == "failed" or result["code"] >= 300:
            raise HomeAssistantError("CLM workflow failed.")
        return result

    hass.services.async_register(DOMAIN, "run_workflow", run_workflow, schema=vol.Schema({
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
        allowed = {item["id"] for item in available if not item["personal"] and not item.get("pending") and item["operation"] not in ("external.run", "automation.run")}
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


async def execute_binding(hass, runtime: Runtime, context, binding: str, operation: str | None = None):
    """Reuse linked-account execution without introducing a second authority path."""
    try:
        actor = runtime.authority.resolve(context.user_id, context.parent_id, binding)
        token = await linked_user(hass, runtime, actor)
        if operation is not None:
            # A workflow call needs a human caller; bridge ingress stays restricted to leaf actions.
            if not context.user_id:
                raise Unauthorized(context=context)
            available = await runtime.client.request("bindings/list", {}, token)
            match = next((item for item in available if item["id"] == binding and not item["personal"] and item["operation"] == operation), None)
            if match is None:
                raise Unauthorized(context=context)
            if match.get("pending"):
                raise HomeAssistantError("Allow this in CLM first: it is waiting under Companion connections.")
        result = await runtime.client.request("bindings/invoke", {"binding": binding, "key": str(uuid4())}, token)
        return {key: result[key] for key in ("id", "status", "code", "duplicate")}
    except Denied:
        raise Unauthorized(context=context) from None
    except CompanionError:
        raise HomeAssistantError("CLM request failed.") from None


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
    from .entities import refresh
    refresh(entry)

    async def changed(hass, entry):
        # A subentry added or deleted under Devices & services: tell CLM now rather than at the next regular exchange.
        if isinstance(entry.runtime_data, Runtime):
            refresh(entry)
            entry.runtime_data.wake.set()
    entry.async_on_unload(entry.add_update_listener(changed))
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
