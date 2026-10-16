"""Outbound bridge reusing existing automation entities and services."""

import asyncio
import hashlib
import itertools
import json
import time

from homeassistant.core import Context, callback
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components.automation import DATA_COMPONENT
from homeassistant.const import EVENT_STATE_CHANGED

# The regular exchange: CLM learns what changed in between from events, so this only catches up.
HEARTBEAT = 300
# The least time between two sends of changed states: a lone change goes at once, a burst together.
SEND_GAP = 0.25
from homeassistant.exceptions import HomeAssistantError

from .client import CompanionError
from . import devices, entities, items


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
    # Catalogue refresh must never delay command delivery or an in-flight automation.
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(publish_catalogue(hass, entry))
        tasks.create_task(receive_dispatches(hass, entry))
        tasks.create_task(forward_events(hass, entry))
        tasks.create_task(forward_states(hass, entry))


async def sync_entities(hass, entry, only: str | None = None):
    """Exchange entity offers and grants for every linked account, or one."""
    runtime = entry.runtime_data
    for actor, credential in list(runtime.links.items()):
        if only is not None and actor != only:
            continue
        try:
            async with asyncio.timeout(60):
                await entities.sync(hass, entry, actor, credential)
        except (CompanionError, HomeAssistantError, TimeoutError, KeyError, TypeError):
            pass


async def forward_states(hass, entry):
    """Sends changes of the entities CLM's workflows use the moment they happen.

    The first change goes at once; changes arriving while it is sent, or within SEND_GAP after, go together in the
    next request, so a sensor reporting many times a second costs a few requests a second, not one per reading. A
    reading that changed twice in between (a number, or attributes only) is sent once, as it is now; every other
    change of state is sent, in order: a press of a button (an event entity), and a value that is set and cleared at
    once, as an action sensor's "4_hold" followed by "" - merging those would lose the press. Other allowed entities' changes wait for the regular exchange.
    An entity appearing or disappearing changes what can be offered, so it wakes the exchange instead."""
    runtime = entry.runtime_data
    waiting: dict[str, dict] = {}
    ready = asyncio.Event()
    presses = itertools.count()

    @callback
    def changed(event):
        state, old = event.data.get("new_state"), event.data.get("old_state")
        if runtime.closed or entities.domain_of(event.data.get("entity_id", "")) not in entities.DOMAINS:
            return
        if state is None or old is None:
            runtime.wake.set()
        if state is None:
            return
        # The main entity of a shared device: the device changed too (a light that is shared as its device).
        if runtime.devices is not None and (reference := runtime.devices.mains.get(state.entity_id)):
            snap = devices.snapshot(hass, reference, runtime.devices.last.get(devices.device_id(reference)))
            for actor in list(runtime.grants):
                if entities.watched(runtime, actor, reference) and len(waiting.setdefault(actor, {})) < 500:
                    waiting[actor][(reference, next(presses)) if separate(state, old) else reference] = snap
                    ready.set()
        key = (state.entity_id, next(presses)) if separate(state, old) else state.entity_id
        for actor in list(runtime.grants):
            if entities.watched(runtime, actor, state.entity_id) and len(waiting.setdefault(actor, {})) < 500:
                waiting[actor][key] = entities.snapshot(state)
                ready.set()

    @callback
    def device_fired(event):
        # A shared device's trigger fired: sent like a press, every firing on its own.
        reference = devices.PREFIX + str(event.data.get("device_id", ""))
        if runtime.closed or runtime.devices is None:
            return
        actors = [actor for actor in list(runtime.grants) if entities.watched(runtime, actor, reference)]
        if not actors:
            return
        snap = devices.snapshot(hass, reference, runtime.devices.last.get(event.data.get("device_id")))
        for actor in actors:
            if len(waiting.setdefault(actor, {})) < 500:
                waiting[actor][(reference, next(presses))] = snap
                ready.set()

    unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, changed)
    unsubscribe_devices = hass.bus.async_listen(devices.EVENT_FIRED, device_fired)
    try:
        while True:
            await ready.wait()
            ready.clear()
            batch, waiting = waiting, {}
            await asyncio.gather(*(send_states(runtime, actor, list(states.values())) for actor, states in batch.items()))
            await asyncio.sleep(SEND_GAP)
    finally:
        unsubscribe()
        unsubscribe_devices()


def separate(state, old) -> bool:
    """Whether a change is sent on its own rather than replaced by a later one: an event, or a state that changed to
    something other than a number. Numbers (a power reading) and attribute-only changes (a dimming light) are merged."""
    if entities.domain_of(state.entity_id) == "event":
        return True
    if old is None or old.state == state.state:
        return False
    try:
        float(state.state)
        return False
    except (TypeError, ValueError):
        return True


async def send_states(runtime, actor: str, states: list[dict]) -> None:
    credential = runtime.links.get(actor)
    if credential is None or not states:
        return
    try:
        await runtime.client.request("entities/states", states, credential)
        entities.remember_sent(runtime, actor, states)
    except CompanionError:
        # Not marked as sent, so the next regular exchange carries them.
        pass


async def forward_events(hass, entry):
    """Forward real automation starts under explicit CLM trigger approvals."""
    runtime = entry.runtime_data
    queue = asyncio.Queue(maxsize=100)

    @callback
    def started(event):
        if runtime.closed or runtime.authority.contains(event.context.parent_id) or runtime.authority.contains(event.context.id):
            return
        component = hass.data.get(DATA_COMPONENT)
        entity = component.get_entity(event.data.get("entity_id")) if component else None
        # Reject synthetic bus events that do not describe the entity's current run.
        if entity is None or not entity.is_on or entity.context.id != event.context.id or not isinstance(entity.raw_config, dict):
            return
        try:
            queue.put_nowait({"automation": entity.entity_id, "fingerprint": fingerprint(entity.raw_config), "key": event.context.id})
        except asyncio.QueueFull:
            pass

    unsubscribe = hass.bus.async_listen("automation_triggered", started)
    active = set()
    try:
        async with asyncio.TaskGroup() as tasks:
            while True:
                while len(active) >= 20:
                    await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                payload = await queue.get()
                task = tasks.create_task(forward_event(hass, runtime, payload))
                active.add(task)
                task.add_done_callback(active.discard)
    finally:
        unsubscribe()


async def forward_event(hass, runtime, payload):
    async def send(actor, credential):
        user = await hass.auth.async_get_user(actor)
        if runtime.closed or user is None or not user.is_active or not user.permissions.check_entity(payload["automation"], POLICY_CONTROL):
            return
        if not entities.granted(runtime, actor, payload["automation"]):
            return
        try:
            await runtime.client.request("external-events", payload, credential)
        except CompanionError:
            # Unknown outcomes are not replayed; unapproved sources are refused by CLM.
            pass
    # Each explicitly linked account has its own trigger approvals. A slow workflow
    # for one account must not postpone another account's event.
    await asyncio.gather(*(send(actor, credential) for actor, credential in list(runtime.links.items())))


async def publish_catalogue(hass, entry):
    """Bounded delivery; errors never log credentials, content or remote responses.

    Runs every five minutes, and early when something it reports changed here: an entity added, removed or
    renamed, automations reloaded, an access entry added or deleted, or CLM asking."""
    runtime = entry.runtime_data
    listed_at, listed_for = 0.0, set()

    @callback
    def changed(event):
        if not runtime.closed:
            # A device or entity added, removed or renamed may change that one device's triggers: only it is read again,
            # never every device, so a busy registry costs Home Assistant next to nothing.
            if runtime.devices is not None and (device := _device_of(hass, event)):
                runtime.devices.forget(device)
            runtime.wake.set()

    unsubscribers = [hass.bus.async_listen(name, changed) for name in ("entity_registry_updated", "device_registry_updated", "automation_reloaded")]
    try:
        await _publish_catalogue(hass, entry, runtime, listed_at, listed_for)
    finally:
        for unsubscribe in unsubscribers:
            unsubscribe()


def _device_of(hass, event) -> str | None:
    """The device a registry change concerns, if any."""
    from homeassistant.helpers import entity_registry as er
    if event.event_type == "device_registry_updated":
        return event.data.get("device_id")
    if event.event_type == "entity_registry_updated":
        item = er.async_get(hass).async_get(event.data.get("entity_id") or "")
        return item.device_id if item else None
    return None


async def _publish_catalogue(hass, entry, runtime, listed_at, listed_for):
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
        except (CompanionError, HomeAssistantError):
            pass
        await sync_entities(hass, entry)
        # The Run CLM item pickers: every ten minutes, and at once for a newly linked account.
        if time.monotonic() - listed_at > 600 or set(runtime.links) != listed_for:
            listed_at, listed_for = time.monotonic(), set(runtime.links)
            await items.refresh(hass, entry)
        # A request from CLM, a subentry change or a change listed above wakes this early.
        try:
            await asyncio.wait_for(runtime.wake.wait(), HEARTBEAT)
            # Several changes usually come together (a reload renames many entities): take them as one.
            await asyncio.sleep(2)
        except TimeoutError:
            pass
        runtime.wake.clear()


async def receive_dispatches(hass, entry):
    runtime = entry.runtime_data
    active = set()
    try:
        async with asyncio.TaskGroup() as tasks:
            while True:
                # Each response reserves at most 20 deliveries. Keep execution bounded.
                while len(active) > 20:
                    await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
                try:
                    deliveries = await runtime.client.request("dispatches/wait", {}, entry.data["auth_token"])
                except CompanionError:
                    await asyncio.sleep(1)
                    continue
                for delivery in deliveries:
                    task = tasks.create_task(deliver(hass, entry, delivery))
                    active.add(task)
                    task.add_done_callback(active.discard)
    finally:
        for task in active:
            task.cancel()


async def deliver(hass, entry, delivery):
    # Reservation is committed before delivery; acknowledgement is never retried.
    accepted = False
    runtime = entry.runtime_data
    try:
        await dispatch(hass, entry, delivery)
        accepted = True
    except (CompanionError, ValueError, HomeAssistantError):
        pass
    try:
        await runtime.client.request("dispatches/acknowledge", {"id": delivery["id"], "completed": accepted}, entry.data["auth_token"])
    except CompanionError:
        pass


async def dispatch(hass, entry, delivery):
    from . import linked_user
    runtime = entry.runtime_data
    kind = delivery.get("kind", "automation")
    if kind == "sync":
        await sync_entities(hass, entry, delivery["actor"])
        return
    if kind == "rescan":
        # The person asked CLM to look for a shared device's triggers again: read them anew, then exchange.
        reference = delivery.get("entity") or ""
        if runtime.devices is None or not devices.is_device(reference) or not entities.granted(runtime, delivery["actor"], reference):
            raise ValueError("Rescan denied.")
        runtime.devices.forget(devices.device_id(reference))
        await sync_entities(hass, entry, delivery["actor"])
        return
    if kind == "entity":
        try:
            await entities.control(hass, entry, delivery)
        except (HomeAssistantError, TimeoutError) as error:
            raise ValueError("Control failed.") from error
        return
    if kind != "automation":
        raise ValueError("Unknown delivery.")
    actor = delivery["actor"]
    credential = await linked_user(hass, runtime, actor)
    user = await hass.auth.async_get_user(actor)
    entity_id = delivery["automation"]
    if delivery["expires"] <= time.time() or not user.permissions.check_entity(entity_id, POLICY_CONTROL) or not entities.granted(runtime, actor, entity_id):
        raise ValueError("Dispatch denied.")
    component = hass.data.get(DATA_COMPONENT)
    entity = component.get_entity(entity_id) if component else None
    if entity is None or not entity.is_on or not isinstance(entity.raw_config, dict) or "use_blueprint" in entity.raw_config or fingerprint(entity.raw_config) != delivery["fingerprint"]:
        raise ValueError("Dispatch denied.")
    bindings = bindings_for(entity.raw_config, entry.entry_id)
    approved = await runtime.client.request("bindings/list", {}, credential)
    allowed = {value["id"] for value in approved if not value["personal"] and not value.get("pending") and value["operation"] not in ("external.run", "automation.run")}
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
