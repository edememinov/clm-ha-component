"""Outbound bridge reusing existing automation entities and services."""

import asyncio
import hashlib
import json
import time

from homeassistant.core import Context
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.automation import DATA_COMPONENT
from homeassistant.exceptions import HomeAssistantError

from .client import CompanionError


def fingerprint(config) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest().upper()


def bindings_for(config, entry_id: str) -> list[str]:
    """Only concrete approved calls qualify; templates and workflow recursion do not."""
    bindings = []
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            action = value.get("action", value.get("service", ""))
            if action == "clm.run_workflow":
                raise ValueError("Workflow recursion is unsupported.")
            if action == "clm.invoke":
                data = value.get("data", {})
                if data.get("config_entry_id") != entry_id or not isinstance(data.get("binding"), str) or "{{" in data["binding"]:
                    raise ValueError("Unsupported binding.")
                bindings.append(data["binding"])
            for child in value.values():
                if isinstance(child, (list, dict)):
                    visit(child)
    visit(config)
    if len(bindings) > 20:
        raise ValueError("Too many bindings.")
    return list(dict.fromkeys(bindings))


async def bridge(hass, entry):
    """Bounded delivery; errors never log credentials, content or remote responses."""
    runtime = entry.runtime_data
    while True:
        try:
            component = hass.data.get(DATA_COMPONENT)
            if component:
                for actor, credential in list(runtime.links.items()):
                    user = await hass.auth.async_get_user(actor)
                    if user is None or not user.is_active:
                        continue
                    automations = []
                    for entity in component.entities:
                        if not user.permissions.check_entity(entity.entity_id, POLICY_CONTROL) or not entity.is_on:
                            continue
                        if not isinstance(entity.raw_config, dict) or "use_blueprint" in entity.raw_config:
                            continue
                        try:
                            bindings_for(entity.raw_config, entry.entry_id)
                        except ValueError:
                            continue
                        automations.append({"id": entity.entity_id, "label": str(entity.name or "Automation")[:128], "fingerprint": fingerprint(entity.raw_config)})
                    await runtime.client.request("external-automations", automations[:100], credential)
            deliveries = await runtime.client.request("dispatches/poll", {}, entry.data["auth_token"])
            # Poll reserves a delivery once. No retry can repeat an automation effect.
            for delivery in deliveries:
                accepted = False
                try:
                    await dispatch(hass, entry, delivery)
                    accepted = True
                except (CompanionError, ValueError, HomeAssistantError):
                    pass
                await runtime.client.request("dispatches/acknowledge", {"id": delivery["id"], "completed": accepted}, entry.data["auth_token"])
        except (CompanionError, HomeAssistantError):
            pass
        await asyncio.sleep(30)


async def dispatch(hass, entry, delivery):
    from . import linked_user
    runtime = entry.runtime_data
    actor = delivery["actor"]
    credential = await linked_user(hass, runtime, actor)
    user = await hass.auth.async_get_user(actor)
    entity_id = delivery["automation"]
    if delivery["expires"] <= time.time() or not user.permissions.check_entity(entity_id, POLICY_CONTROL):
        raise ValueError("Dispatch denied.")
    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component else None
    if entity is None or not entity.is_on or not isinstance(entity.raw_config, dict) or "use_blueprint" in entity.raw_config or fingerprint(entity.raw_config) != delivery["fingerprint"]:
        raise ValueError("Dispatch denied.")
    bindings = bindings_for(entity.raw_config, entry.entry_id)
    approved = await runtime.client.request("bindings/list", {}, credential)
    allowed = {value["id"] for value in approved if not value["personal"] and value["operation"] not in ("external.run", "automation.run")}
    if any(binding not in allowed for binding in bindings):
        raise ValueError("Dispatch denied.")
    root = Context(user_id=actor)
    runtime.authority.register(root.id, actor, bindings)
    try:
        async with asyncio.timeout(60):
            await hass.services.async_call("automation", "trigger", {"entity_id": entity_id, "skip_condition": False}, blocking=True, context=root)
    except (HomeAssistantError, TimeoutError):
        raise ValueError("Dispatch outcome unavailable.") from None
    finally:
        runtime.authority.remove(root.id)
