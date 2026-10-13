"""Home Assistant entities a linked CLM account may use, each only after it is allowed here.

CLM asks for an entity and Home Assistant answers through a Repairs item; an administrator can also
offer one. Every allowed entity is a subentry of the connection, so removing it under Devices &
services revokes it. CLM holds the same list; this side decides when the two disagree.
"""

from datetime import datetime
import json
from types import MappingProxyType
from time import monotonic

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

DOMAINS = ("light", "button", "input_button", "sensor", "binary_sensor")
SERVICES = {"light": ("turn_on", "turn_off", "toggle"), "button": ("press",), "input_button": ("press",)}
SUBENTRY = "access"
MAX_ENTITIES = 1000
EVENT_REQUESTED = "clm_access_requested"


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


def policy(entity_id: str) -> str:
    """Sensors are read; lights, buttons and automations are used."""
    return POLICY_CONTROL if domain_of(entity_id) in (*SERVICES, "automation") else POLICY_READ


def permitted(user, entity_id: str) -> bool:
    return (user is not None and user.is_active and domain_of(entity_id) in (*DOMAINS, "automation")
            and user.permissions.check_entity(entity_id, policy(entity_id)))


def held(entry) -> dict[str, ConfigSubentry]:
    """Allowed grants by CLM grant ID."""
    return {sub.data["grant"]: sub for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY and isinstance(sub.data.get("grant"), str)}


def index(entry) -> dict[str, set[str]]:
    """Allowed entity IDs per linked account."""
    result: dict[str, set[str]] = {}
    for sub in held(entry).values():
        result.setdefault(sub.data["actor"], set()).add(sub.data["entity"])
    return result


def granted(runtime, actor: str, entity_id: str) -> bool:
    return entity_id in runtime.grants.get(actor, ())


def catalogue(hass, user) -> list[dict]:
    """What the account could use: names only, never states, until something is allowed."""
    rows = [{"id": state.entity_id, "label": (state.name or state.entity_id)[:128].strip() or state.entity_id, "domain": state.domain}
            for state in hass.states.async_all(DOMAINS) if permitted(user, state.entity_id)]
    return sorted(rows, key=lambda row: row["label"].casefold())[:MAX_ENTITIES]


def _scalar(value):
    if isinstance(value, bool) or value is None or isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return value[:255]
    if isinstance(value, datetime):
        return value.isoformat()
    return None


def snapshot(state) -> dict:
    """A state as CLM conditions read it: scalar attributes and short number lists such as colours."""
    attributes = {}
    for key, value in list(state.attributes.items())[:40]:
        if isinstance(value, (list, tuple)) and len(value) <= 4 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
            attributes[key] = list(value)
        elif (clean := _scalar(value)) is not None or value is None:
            attributes[key] = clean
    while attributes and len(json.dumps(attributes, default=str)) > 4000:
        attributes.popitem()
    return {"id": state.entity_id, "state": str(state.state)[:255], "attributes": attributes, "changed": state.last_changed.isoformat()}


def service_data(service: str, data: dict | None) -> dict:
    """The light options CLM may send, checked again here; anything else is refused."""
    data = data or {}
    if data and service != "turn_on":
        raise ValueError("Options are only accepted when turning on.")
    def whole(value, low, high):
        return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    checks = {
        "brightness_pct": lambda value: whole(value, 0, 100),
        "color_temp_kelvin": lambda value: whole(value, 1000, 12000),
        "transition": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 300,
        "rgb_color": lambda value: isinstance(value, list) and len(value) == 3 and all(whole(item, 0, 255) for item in value),
    }
    if any(key not in checks or not checks[key](value) for key, value in data.items()) or {"rgb_color", "color_temp_kelvin"} <= set(data):
        raise ValueError("Unsupported light options.")
    return dict(data)


def refresh(entry) -> None:
    runtime = entry.runtime_data
    runtime.grants = index(entry)
    # A just-allowed grant only needs covering until its subentry exists; after that,
    # deleting the subentry must count as a revocation at once.
    stored = set(held(entry))
    runtime.fresh = {grant: until for grant, until in runtime.fresh.items() if grant not in stored and until > monotonic()}


def add(hass, entry, actor: str, user, grant: str, entity_id: str, label: str) -> None:
    if grant in held(entry):
        return
    hass.config_entries.async_add_subentry(entry, ConfigSubentry(
        data=MappingProxyType({"grant": grant, "actor": actor, "entity": entity_id}),
        subentry_type=SUBENTRY, title=f"{label} · {user.name or 'CLM account'}", unique_id=grant))
    refresh(entry)


def _remember(runtime, grant: str) -> None:
    # A grant allowed a moment ago counts as held until its subentry exists, so a sync
    # running in between does not tell CLM it was revoked.
    now = monotonic()
    runtime.fresh = {key: until for key, until in runtime.fresh.items() if until > now}
    runtime.fresh[grant] = now + 60


def label_of(hass, entity_id: str, fallback: str | None = None) -> str:
    """HA's own name; before the entity has a state (just after a restart), the name it was offered under."""
    state = hass.states.get(entity_id)
    return (state.name if state else None) or (fallback or "")[:128].strip() or entity_id


def issue_id(grant: str) -> str:
    return f"access_{grant}"


def ask(hass, entry, actor: str, user, request: dict) -> None:
    """Puts CLM's request in Repairs, and the first time fires an event automations can notify with."""
    registry = ir.async_get(hass)
    existing = registry.async_get_issue(DOMAIN, issue_id(request["id"]))
    # HA keeps issues across restarts but shows only the ones created again since starting.
    if existing is not None and existing.active:
        return
    entity_id = request["entity"]
    ir.async_create_issue(
        hass, DOMAIN, issue_id(request["id"]), is_fixable=True, is_persistent=False, severity=ir.IssueSeverity.WARNING,
        translation_key="access_request_control" if policy(entity_id) == POLICY_CONTROL else "access_request_read",
        translation_placeholders={"entity": label_of(hass, entity_id, request.get("label")), "entity_id": entity_id, "account": user.name or "CLM account"},
        data={"entry_id": entry.entry_id, "actor": actor, "grant": request["id"], "entity": entity_id, "label": str(request.get("label") or "")[:128]})
    if existing is None:
        hass.bus.async_fire(EVENT_REQUESTED, {"entity_id": entity_id, "account": user.name or "", "config_entry_id": entry.entry_id})


async def sync(hass, entry, actor: str, credential: str) -> None:
    """One exchange for one account: the offer, allowed states and grants out; CLM's grants back."""
    runtime = entry.runtime_data
    user = await hass.auth.async_get_user(actor)
    if runtime.closed or user is None or not user.is_active:
        return
    async with runtime.grant_lock:
        mine = {grant: sub for grant, sub in held(entry).items() if sub.data["actor"] == actor}
        migrated = set(entry.data.get("grants_migrated", []))
        migrate = actor not in migrated
        now = monotonic()
        keep = set(mine) | {grant for grant, until in runtime.fresh.items() if until > now}
        states = [snapshot(state) for sub in mine.values() if domain_of(sub.data["entity"]) in DOMAINS
                  and (state := hass.states.get(sub.data["entity"])) is not None]
        result = await runtime.client.request("entities/sync", {
            "entities": catalogue(hass, user), "states": states, "grants": sorted(keep), "migrate": migrate}, credential)
        listed = {grant["id"]: grant for grant in result.get("grants", []) if isinstance(grant, dict)}
        for grant, sub in mine.items():
            if grant not in listed:
                hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
        pending = set()
        for grant in listed.values():
            entity_id = grant.get("entity", "")
            if grant.get("status") == "pending":
                if not permitted(user, entity_id):
                    # Nothing to ask about: this account could not use it here anyway.
                    await runtime.client.request("entities/decision", {"grant": grant["id"], "allow": False}, credential)
                    continue
                pending.add(grant["id"])
                ask(hass, entry, actor, user, grant)
            elif grant.get("status") == "approved" and grant["id"] not in mine and migrate and grant.get("carried") and permitted(user, entity_id):
                add(hass, entry, actor, user, grant["id"], entity_id, label_of(hass, entity_id))
        registry = ir.async_get(hass)
        for (domain, key), issue in list(registry.issues.items()):
            data = issue.data or {}
            if domain == DOMAIN and data.get("entry_id") == entry.entry_id and data.get("actor") == actor and data.get("grant") not in pending:
                ir.async_delete_issue(hass, DOMAIN, key)
        if migrate:
            hass.config_entries.async_update_entry(entry, data={**entry.data, "grants_migrated": sorted(migrated | {actor})})
        refresh(entry)


async def answer(hass, entry, actor: str, grant: str, entity_id: str, allow: bool) -> None:
    """An administrator's answer from Repairs. Allowing adds the grant under the connection."""
    from . import linked_user
    runtime = entry.runtime_data
    credential = await linked_user(hass, runtime, actor)
    user = await hass.auth.async_get_user(actor)
    if allow and not permitted(user, entity_id):
        raise Unauthorized()
    async with runtime.grant_lock:
        await runtime.client.request("entities/decision", {"grant": grant, "allow": allow}, credential)
        if allow:
            _remember(runtime, grant)
            add(hass, entry, actor, user, grant, entity_id, label_of(hass, entity_id))


async def offer(hass, entry, actor: str, entity_id: str) -> str:
    """An administrator sharing an entity without being asked; the subentry flow stores the result."""
    from . import linked_user
    runtime = entry.runtime_data
    credential = await linked_user(hass, runtime, actor)
    user = await hass.auth.async_get_user(actor)
    if not permitted(user, entity_id):
        raise Unauthorized()
    async with runtime.grant_lock:
        result = await runtime.client.request("entities/offer", {"entity": entity_id, "label": label_of(hass, entity_id)[:128]}, credential)
        _remember(runtime, result["id"])
        return result["id"]


async def control(hass, entry, delivery: dict) -> None:
    """Carries out CLM's service call on an allowed entity as the linked account."""
    import time
    from . import linked_user
    runtime = entry.runtime_data
    actor = delivery["actor"]
    await linked_user(hass, runtime, actor)
    user = await hass.auth.async_get_user(actor)
    entity_id = delivery.get("entity") or ""
    service = delivery.get("service") or ""
    if (delivery["expires"] <= time.time() or not granted(runtime, actor, entity_id) or service not in SERVICES.get(domain_of(entity_id), ())
            or not user.permissions.check_entity(entity_id, POLICY_CONTROL) or hass.states.get(entity_id) is None):
        raise ValueError("Control denied.")
    data = service_data(service, delivery.get("data"))
    await hass.services.async_call(domain_of(entity_id), service, {"entity_id": entity_id, **data}, blocking=True, context=Context(user_id=actor))
