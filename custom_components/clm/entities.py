"""Home Assistant entities a linked CLM account may use, each only after it is allowed.

CLM asks for an entity; the linked person answers in the CLM card's Approvals tab, and this side
confirms that their Home Assistant user may use it before it counts. An administrator can also offer
one. Every allowed entity is a subentry of the connection, so removing it under Devices & services
revokes it. CLM holds the same list; this side decides when the two disagree.
"""

from datetime import datetime
import hashlib
import json
from types import MappingProxyType
from time import monotonic

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import issue_registry as ir

from .client import CompanionError
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


def traits(state) -> dict:
    """What an entity can do, which does not change while it exists, so CLM can offer choices instead of raw values.

    A light's colour modes and white range, a sensor's device class, unit and options. Never a reading."""
    attributes, found = state.attributes, {}
    if state.domain == "light":
        modes = attributes.get("supported_color_modes")
        if isinstance(modes, (list, tuple, set, frozenset)):
            found["modes"] = sorted(str(mode)[:32] for mode in modes)[:8]
        for key, name in (("min_color_temp_kelvin", "min_kelvin"), ("max_color_temp_kelvin", "max_kelvin")):
            if isinstance(attributes.get(key), int) and not isinstance(attributes.get(key), bool):
                found[name] = attributes[key]
    elif state.domain in ("sensor", "binary_sensor"):
        for key, name in (("device_class", "device_class"), ("unit_of_measurement", "unit"), ("state_class", "state_class")):
            if isinstance(attributes.get(key), str) and attributes[key]:
                found[name] = attributes[key][:64]
        options = attributes.get("options")
        if state.domain == "sensor" and isinstance(options, (list, tuple)) and all(isinstance(option, str) for option in options):
            found["options"] = [option[:64] for option in options[:32]]
    while found and len(json.dumps(found)) > 2000:
        found.popitem()
    return found


def catalogue(hass, user) -> list[dict]:
    """What the account could use: names and what each can do, never states, until something is allowed."""
    rows = []
    for state in hass.states.async_all(DOMAINS):
        if permitted(user, state.entity_id):
            row = {"id": state.entity_id, "label": (state.name or state.entity_id)[:128].strip() or state.entity_id, "domain": state.domain}
            if found := traits(state):
                row["traits"] = found
            rows.append(row)
    return sorted(rows, key=lambda row: row["label"].casefold())[:MAX_ENTITIES]


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def signature(snap: dict) -> str:
    """A state as sent, without when it changed: equal signatures need not be sent again."""
    return digest([snap["state"], snap["attributes"]])


def memo(runtime) -> dict:
    """What was last exchanged per account: the offer's fingerprint, each state's signature, and CLM's watch list."""
    found = getattr(runtime, "exchange", None)
    if not isinstance(found, dict) or not found:
        found = {"hash": {}, "sent": {}, "watch": {}}
        runtime.exchange = found
    return found


def watched(runtime, actor: str, entity_id: str) -> bool:
    """Whether a change is sent the moment it happens: CLM names the entities its workflows use. An older CLM names
    none, and then every allowed entity counts."""
    watch = memo(runtime)["watch"].get(actor)
    return entity_id in runtime.grants.get(actor, ()) and (watch is None or entity_id in watch)


def remember_sent(runtime, actor: str, snaps) -> None:
    sent = memo(runtime)["sent"].get(actor)
    if sent is not None:
        for snap in snaps:
            sent[snap["id"]] = signature(snap)


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


def _clear_repairs(hass, entry) -> None:
    """Requests used to wait in Repairs; they are answered in the CLM card now, so any left there go."""
    registry = ir.async_get(hass)
    for domain, key in list(registry.issues):
        issue = registry.issues[(domain, key)]
        if domain == DOMAIN and key.startswith("access_") and (issue.data or {}).get("entry_id", entry.entry_id) == entry.entry_id:
            ir.async_delete_issue(hass, DOMAIN, key)


async def sync(hass, entry, actor: str, credential: str) -> None:
    """One exchange for one account: grants out, CLM's grants back.

    The offer goes only when it changed since CLM last took it, and states only when they changed since last
    sent; the first exchange after starting sends every state. When CLM says it lost what it held (it restarted),
    the exchange is repeated once with everything. CLM answers which entities its workflows use: only their
    changes are sent the moment they happen (see bridge.forward_states)."""
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
        exchanged = memo(runtime)
        rows = catalogue(hass, user)
        offer = digest(rows)
        full = False
        for attempt in range(2):
            previous = exchanged["sent"].get(actor)
            complete = full or previous is None
            current = [snapshot(state) for sub in mine.values() if domain_of(sub.data["entity"]) in DOMAINS
                       and (state := hass.states.get(sub.data["entity"])) is not None]
            states = current if complete else [snap for snap in current if previous.get(snap["id"]) != signature(snap)]
            payload = {"states": states, "statesComplete": complete, "grants": sorted(keep), "migrate": migrate, "hash": offer}
            if full or exchanged["hash"].get(actor) != offer:
                payload["entities"] = rows
            try:
                result = await runtime.client.request("entities/sync", payload, credential)
            except CompanionError:
                if "entities" in payload:
                    raise
                # A CLM from before offer fingerprints needs the whole offer every time.
                result = await runtime.client.request("entities/sync", {**payload, "entities": rows}, credential)
            exchanged["hash"][actor] = offer
            exchanged["sent"][actor] = {**({} if complete else previous), **{snap["id"]: signature(snap) for snap in states}}
            if attempt or not result.get("need"):
                break
            full = True
        watch = result.get("watch")
        if isinstance(watch, list):
            exchanged["watch"][actor] = {value for value in watch if isinstance(value, str)}
        else:
            exchanged["watch"].pop(actor, None)
        listed = {grant["id"]: grant for grant in result.get("grants", []) if isinstance(grant, dict)}
        for grant, sub in mine.items():
            if grant not in listed:
                hass.config_entries.async_remove_subentry(entry, sub.subentry_id)
        for grant in listed.values():
            entity_id, status = grant.get("entity", ""), grant.get("status")
            if status in ("pending", "confirming") and not permitted(user, entity_id):
                # This account could not use it here anyway, whatever the person answers.
                await runtime.client.request("entities/decision", {"grant": grant["id"], "allow": False}, credential)
            elif status == "pending" and grant["id"] not in runtime.asked:
                # Answered in the CLM card; the event lets an automation say so on a phone.
                runtime.asked.add(grant["id"])
                hass.bus.async_fire(EVENT_REQUESTED, {"entity_id": entity_id, "account": user.name or "", "config_entry_id": entry.entry_id})
            elif status == "confirming":
                # The person allowed it in the CLM card; this account may use it, so it counts from now.
                await runtime.client.request("entities/decision", {"grant": grant["id"], "allow": True}, credential)
                _remember(runtime, grant["id"])
                add(hass, entry, actor, user, grant["id"], entity_id, label_of(hass, entity_id, grant.get("label")))
            elif status == "approved" and grant["id"] not in mine and migrate and grant.get("carried") and permitted(user, entity_id):
                add(hass, entry, actor, user, grant["id"], entity_id, label_of(hass, entity_id))
        _clear_repairs(hass, entry)
        if migrate:
            hass.config_entries.async_update_entry(entry, data={**entry.data, "grants_migrated": sorted(migrated | {actor})})
        refresh(entry)


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
