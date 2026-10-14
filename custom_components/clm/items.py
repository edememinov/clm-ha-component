"""CLM items in HA automations: picked by name in HA's action editor, run as the linked account.

The pickers list what each linked account may run in CLM (never personal items, which need a PIN).
CLM checks the account's current permissions on every run, so a list that is a few minutes old can
offer something CLM then refuses, never the other way round.
"""

from uuid import uuid4

import probatio as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_set_service_schema

from .client import CompanionError
from .const import DOMAIN

SERVICE = "run_item"
MAX_ITEMS = 500
PAGE = 50


def item_value(reference: dict) -> str:
    return f"{reference['kind']}:{reference['id']}"


def kind_label(kind: str) -> str:
    return kind.replace("-", " ").capitalize()


async def fetch(runtime, credential: str) -> tuple[list[dict], dict[str, str]]:
    """One account's runnable items and the labels of their actions."""
    capabilities = await runtime.client.request("capabilities", {}, credential)
    labels = {value["id"]: value["label"] for value in capabilities.get("operations", []) if isinstance(value, dict)}
    items, offset = [], 0
    while len(items) < MAX_ITEMS:
        page = await runtime.client.request("catalog/query", {"search": "", "offset": offset}, credential)
        items += [value for value in page.get("items", []) if isinstance(value, dict) and isinstance(value.get("operations"), list) and value["operations"]
                  and isinstance(value.get("label"), str) and isinstance(value.get("reference"), dict)
                  and isinstance(value["reference"].get("kind"), str) and isinstance(value["reference"].get("id"), str)]
        if not page.get("more"):
            break
        offset += PAGE
    return items[:MAX_ITEMS], labels


async def refresh(hass, entry) -> None:
    """Re-reads every linked account's items, then rebuilds the action's pickers."""
    runtime = entry.runtime_data
    for actor, credential in list(runtime.links.items()):
        try:
            runtime.items[actor], labels = await fetch(runtime, credential)
            runtime.operations.update(labels)
        except (CompanionError, KeyError, TypeError):
            continue
    for actor in set(runtime.items) - set(runtime.links):
        runtime.items.pop(actor, None)
    describe(hass)


def describe(hass) -> None:
    from . import Runtime
    items: dict[str, str] = {}
    operations: dict[str, str] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if not isinstance(runtime, Runtime):
            continue
        for rows in runtime.items.values():
            for row in rows:
                items.setdefault(item_value(row["reference"]), f"{row['label']} ({kind_label(row['reference']['kind'])})")
                for operation in row["operations"]:
                    operations.setdefault(operation, runtime.operations.get(operation, operation))
    def options(values: dict[str, str]):
        return [{"value": value, "label": label} for value, label in sorted(values.items(), key=lambda pair: pair[1].casefold())]
    async_set_service_schema(hass, DOMAIN, SERVICE, {
        "name": "Run CLM item",
        "description": "Runs an action on a CLM item with the linked account's current CLM permissions. Items are listed by name; reload the page after adding items in CLM.",
        "fields": {
            "config_entry_id": {"required": True, "selector": {"config_entry": {"integration": DOMAIN}}},
            "item": {"name": "CLM item", "required": True, "description": "What to act on. Only items the linked account may use in CLM are listed.",
                     "selector": {"select": {"options": options(items), "mode": "dropdown", "custom_value": True}}},
            "operation": {"name": "Action", "required": True, "description": "What to do. CLM refuses an action the item does not support.",
                          "selector": {"select": {"options": options(operations), "mode": "dropdown", "custom_value": True}}},
            "inputs": {"name": "Plugin inputs", "advanced": True, "description": "Values for a plugin action that asks for them.",
                       "selector": {"object": {}}},
            "destination": {"name": "CLM window", "advanced": True, "description": "For actions that open something in a CLM window: {device, window}.",
                            "selector": {"object": {}}},
            "account_user_id": {"name": "Linked HA account", "advanced": True,
                                "description": "Optional when one account is linked. Required for unattended actions when multiple accounts are linked.",
                                "selector": {"text": {}}},
        },
    })


def register(hass):
    async def run(call):
        from . import _runtime
        from .native_actions import action_account
        runtime = _runtime(hass, call.data["config_entry_id"])
        token = await action_account(hass, runtime, call.context, call.data.get("account_user_id"))
        kind, _, identifier = call.data["item"].partition(":")
        if not kind or not identifier:
            raise HomeAssistantError("Choose a CLM item from the list.")
        arguments = {}
        if call.data.get("inputs"):
            arguments["values"] = call.data["inputs"]
        if call.data.get("destination"):
            arguments["destination"] = call.data["destination"]
        try:
            result = await runtime.client.request("commands", {
                "value": {"operation": call.data["operation"], "target": {"kind": kind, "id": identifier}, "arguments": arguments},
                "key": str(uuid4())}, token)
        except CompanionError:
            raise HomeAssistantError("CLM refused or could not run this action. Check the item, the action and the linked account's CLM permissions.") from None
        if result.get("status") == "failed" or result.get("code", 200) >= 300:
            raise HomeAssistantError("The CLM action failed.")
        return {key: result[key] for key in ("id", "status", "code", "duplicate") if key in result}

    hass.services.async_register(DOMAIN, SERVICE, run, schema=vol.Schema({
        vol.Required("config_entry_id"): str,
        vol.Required("item"): vol.All(str, vol.Length(min=3, max=300)),
        vol.Required("operation"): vol.All(str, vol.Length(min=1, max=200)),
        vol.Optional("inputs"): dict,
        vol.Optional("destination"): dict,
        vol.Optional("account_user_id"): str,
    }), supports_response=SupportsResponse.OPTIONAL)
    describe(hass)
