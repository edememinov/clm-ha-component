# Integration architecture

This repository owns the native Home Assistant adapter. It is independent of
the CLM application and card repositories. Supported API baseline: Core 2026.10.0.

Config flows use ConfigFlow and native selectors. Private WebSocket commands
derive identity exclusively from connection.user. Service actions derive identity
from call.context, then require current active HA users and individual CLM links.
HA administration grants no CLM account access. No personal content is published
to entities, state, recorder, service descriptions or events.

The client uses verified HTTPS, or an explicitly configured HTTP base URL for
trusted local/Docker transport, with bounded requests and redacted failures.
Installation credentials carry transport authority only. Individual link
credentials remain in HA-owned storage; infrastructure operators are trusted.
No PIN is stored. UI credentials are one-use grants for a specified frame nonce
and approved parent origin, passed through private WebSocket responses.

Services contain opaque bindings only. Unregistered anonymous contexts are
rejected. Native automation execution is reused with conditions enabled; no
monkey patch, event marker, trace scraping or provisional automation API is used.
Unsupported unattended provenance paths remain denied.

HA setup, storage, selectors, service responses and WebSocket APIs own platform
behavior. The companion client and authority policy are independently testable.
The Lovelace shell and CLM workspace have their own owners and contain no copied
application controls here.

`config_flow.py` owns native selectors and reconfiguration; `strings.json` and
translations own labels and `data_description` help (HA renders these below
inputs, rather than exposing a tooltip customization hook). `addresses.py` owns
HA network-helper discovery and exact approved-origin selection. Internal,
external and cloud URLs are discovered at setup/reconfigure, not trusted from
WebSocket payloads. The browser supplies its current audience and presentation
mode; `websocket.py` validates both and selects only configured workspace URLs.
It also validates the browser's selected fallback against the bounded candidate
list; arbitrary caller-supplied workspace URLs are rejected. Automatic mode
offers local hostname, local IP fallback and default in order. HTTPS audiences
receive HTTPS candidates only, irrespective of whether HA uses a public URL.
Mixed HTTPS/HTTP embedding is rejected, with an HTTPS default fallback in auto
mode. Existing entries retain their one approved audience until reconfigured.
Reconfiguration updates the remote approved origins using installation transport
authority and reloads HA without replacing account storage or installation IDs.

Each URL is validated independently in `config_flow.py`; native form errors are
keyed to the affected field. Extra HA overrides are advanced-only unless
discovery fails, and hidden saved overrides survive reconfiguration. The client
uses a read-only GET probe of the POST-only origins route to detect older running
backends before registration, without cookies/account authority or persistent
side effects. Error reasons are bounded codes; response bodies and secrets never
enter UI errors. Labels/help remain native translation resources and require a
fresh browser translation session after updating/restarting the integration.

Verification for 0.1.2: 35 HA tests cover per-field URL errors, advanced override
visibility/preservation, read-only compatibility checks, candidate membership and
HTTPS filtering. Native HA frontend controls rendered with fixture schemas at
1440/768/390 px placed errors beside the correct fields, hid advanced overrides
and showed no horizontal overflow. This uses the platform's actual frontend
components; it is not an authenticated live setup/reconfiguration test. Captures
and fixture scripts stay outside repositories in `E:/CLM/.tmp/ha-address-native-ui`.

`.github/workflows/release.yml` validates push/PR changes and publishes releases
only after successful checks on main. The manifest owns the release version;
published tags are immutable, and component changes without a version bump fail.
Release creation uses the workflow's scoped contents-write token, starts as a
draft and publishes only after preparation. Reruns skip identical published
versions; no archive packaging or execution credentials are involved.

## Workflow execution actions — 0.1.3

`__init__.py` owns `clm.list_bindings` (response required) and
`clm.run_workflow` (optional bounded outcome response). Both are registered in
`async_setup` using native HA schemas and selectors. `services.yaml`,
`strings.json`, `translations/en.json` and `icons.json` own action metadata;
there is no copied UI control or stylesheet. `run_automation` keeps its existing
service ID and behavior but is labeled Run Home Assistant automation, making the
execution direction explicit.

`execute_binding` is the one linked-account invocation owner shared with
`clm.invoke`. The workflow action validates a currently listed non-personal
`automation.run` binding, then delegates to the existing `bindings/invoke`
transport with a fresh idempotency key. Only authenticated user contexts qualify;
no actor or PIN parameter, installation authority, account fallback, additional
credential store or entity publication is introduced. Backend invocation still
rechecks permissions, expiry, ownership and workflow state. Failed workflow
results raise a neutral HA error; unknown network outcomes are not retried.
Listing returns only ID, operation and expiry for the linked user's non-personal
approvals, filtered to workflows by default.

`bridge.bindings_for` rejects `clm.run_workflow` even in nested HA action blocks.
The existing outbound bridge remains limited to approved leaf bindings; the new
service cannot bypass its workflow-cycle restriction through an inherited HA
context. Existing `clm.invoke` ingress behavior is preserved.

Unattended HA schedules/sensor events use the existing MQTT trigger rather than
inventing authenticated provenance. No backend contract or binding scope changes
are needed. The execution [README](docs/automation-execution/README.md) documents
account approval, native actions, dashboard buttons, MQTT/HTTP alternatives,
responses, expiry and troubleshooting. Existing owners and contracts are reused;
no new execution abstraction or persistence layer is necessary.

Verification: 47 component tests passed on Python 3.14.3; compilation and
`git diff --check` passed. Ten handler tests cover invocation, linked-account
scope, workflow type, personal/anonymous denial, listing, failures, native schema
fields and existing bounded leaf ingress. Bridge and metadata tests cover nested
cycle rejection and action translations/icons. Native frontend verification was
attempted against login-loaded HA assets, which expose `ha-form` but not the
`ha-selector` element required by these forms; no visual pass is claimed. No live
HA action invocation or deployment was performed. Install/restart the updated
component for the new native Actions entries to become available.

Reference documentation:

- https://developers.home-assistant.io/docs/auth_permissions/
- https://developers.home-assistant.io/docs/core/integration/config_flow/
- https://developers.home-assistant.io/docs/dev_101_services/
- https://developers.home-assistant.io/docs/frontend/data/
- https://developers.home-assistant.io/docs/device_automation_trigger/
- https://developers.home-assistant.io/docs/automations/

The last interface is provisional and is excluded from implementation.

## HA-owned native worker actions

`native_actions.py` automatically registers start_session/open_selkies/open_clm
and list_devices during async_setup. Native HA service selectors, translations
and icons supply the action fields. Interactive callers retain their own account;
unattended callers select one explicitly when multiple links exist. The supplied
PIN is forwarded only to the CLM native action adapter and removed from the
request dictionary afterward; HA action data/traces may contain it. No view token,
installation credential, saved workflow or shared admin fallback authorizes these
actions. `docs/native-actions.md` owns setup, inputs, examples and delivery limits.
The backend owns authorization/selection/queueing; the existing Windows worker
opens validated URLs with fixed normal/private browser arguments.

## Immediate delivery and selected external triggers — 0.2.1

`bridge.py` composes independent catalogue publication, long-poll delivery and
automation-start event forwarding in one cancellable TaskGroup. Delivery reserves
the existing persisted queue once; bounded concurrent tasks do not wait behind a
long automation. The new `/dispatches/wait` lease lasts at most 20 seconds and
wakes on queue commit. Empty lease responses reconnect immediately; network
failures back off for one second. Catalogue refresh remains every 30 seconds.

The native `automation_triggered` listener checks the enabled entity's current
context, hashes its current definition and suppresses bridge-origin contexts
through `Authority.contains`. It queues only entity ID, fingerprint and opaque
context key; a bounded queue of 100 and at most 20 event-forwarding tasks prevent
unbounded work. Each linked active account must have HA entity-control permission;
CLM further requires that account's reviewed reference and an enabled non-personal
workflow explicitly selecting it. Anonymous ordinary service ingress stays denied.
No event label, PIN or execution arguments are forwarded, and effects are never
retried on network failure. Unload cancels tasks and removes the event listener.

`client.py` owns the two additive transport routes. Event calls have a 330-second
completion timeout for existing workflow delays; this is not a trigger latency
interval. The backend starts matching workflows concurrently under its current
execution limits. Backend/adapter upgrade together is required. Current native
HA action schemas, controls, translations and stored links are unchanged.

Local tests cover millisecond event forwarding/concurrent delivery, synthetic and
bridge-context suppression, inactive/unpermitted users, cancellation/unsubscribe
and failed acknowledgements without replay. Backend local TestServer/SQLite tests
measure both directions and verify stable concurrent approvals and event replay.
Production is excluded from verification by user instruction.

Verification: 56 local component tests passed. `test_bridge_delivery.py` was added
for event forwarding, concurrent delivery/account fan-out, lifecycle and denial
checks; existing platform/service/policy tests remain passing. Final fake-I/O
measurements were 2.065 ms from event callback to outbound request and 3.337 ms
from wait response to concurrent execution. The corresponding real local CLM
TestServer/SQLite measurements were 24.031 ms incoming and 51.015 ms outgoing.
No production access or publication was performed. Event semantics were checked
against [Core 2026.10.0 automation source](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/components/automation/__init__.py):
the start event is emitted after conditions pass and the action script starts.

## Browser notification workflows

No new HA runtime owner: `execute_binding`/`run_workflow` retain linked-user provenance,
opaque IDs, redacted bounded outcomes and the outbound workflow-cycle denial. Unattended
schedules/sensors use the existing authorized MQTT trigger. CLM owns subscriptions,
VAPID identity, device targeting and worker tap intent; HA never stores push keys or
notification content. `docs/automation-execution/README.md` documents the additive
workflow action. `test_services.py` checks that acceptance returns only the existing
outcome fields and ingress contains only binding/key. See backend Web Push evidence
for the real companion execution adapter and MQTT integration checks.

## Devices — 0.8.0

`devices.py` offers devices with triggers as `device.<device id>` grants (domain `device`, read policy; `devices.permitted`: administrator, or read permission on one of the device's entities). `DeviceTriggers` reads `async_get_device_automations` once per device (cached, forgotten on `device_registry_updated` / `entity_registry_updated`), keys each trigger (`trigger_key`) and words it with the integration's `device_automation` translations, else `"<subtype>" <type>` as Home Assistant's frontend; entity triggers name the entity through the registry (device triggers carry registry ids). `traits` carry `triggers` and, from the main entity (`main_entity`: first entity in `SERVICES` order without an entity category), its `kind` and `entities.traits`. `attach` initialises the triggers of watched devices only (`entities.watched_devices`, after each exchange); a firing records `last` and fires `clm_device_trigger`, which `forward_states` sends on its own. `snapshot` is the main entity's state and attributes plus `trigger`, `label`, `triggered`; a main entity's change is sent as the device's too (`mains`). `entities.control` sends a device's service call to its main entity. Checked in a real Core 2026.10.0 with a ZHA-shaped remote and a light: offered with labels and kind, firings sent, light state carried, detaching works. Tests: `test_entities.py` (offer, baseline, attach only watched, control through the light), `test_bridge_states.py` (every firing sent).

## Buttons and immediate forwarding — 0.7.0

`event` is a read-only domain (`entities.DOMAINS`); its `event_types` and device class travel as traits. `event.py` is the integration's one entity platform: on start and on `device_registry_updated` (only the device concerned) it asks `async_get_device_automations` for device triggers and gives each enabled device with press triggers (platform `device`, no `entity_id`, not `device_offline`) and no event entity of another integration a `ButtonPresses` entity. The entity sets `device_entry` to the existing device, so the device stays its own integration's; it attaches the presses with `async_validate_trigger_config` / `async_initialize_triggers`, one trigger per press with the press name as trigger `id`, and fires `_trigger_event`. `press_name` turns ZHA's `remote_button_short_press` + `button_1` into `button_1_short_press` and an `action` trigger into its subtype. `forward_states` sends the first change at once, then waits `SEND_GAP` (0.25 s) before the next send; a state that changed twice in between goes once, every event-entity press goes in order. Registry access iterates `DeviceRegistry.devices` (mapping access is deprecated in 2026.10). Tests: `test_button_presses.py`, `test_bridge_states.py` (immediate send, burst, every press), `test_entities.py`; 81 pass. Checked in a real Core 2026.10.0 with a ZHA-shaped four-button remote: the entity is attached to the remote's device, which keeps its owner; 12 press types; each press changes the state within 0.3 ms, the same press twice included.

## Change-driven exchange — 0.5.0

`entities.sync` sends the offer only when its fingerprint (`digest` of the `catalogue` rows) differs from the one CLM took, and states only when their `signature` (state plus attributes) changed since last sent; the first exchange after starting, and any exchange after CLM answers `need`, is repeated once with everything (`statesComplete`). An older CLM refusing an exchange without the offer gets it in full. `catalogue` rows carry `traits` (light `supported_color_modes` and white range; sensor device class, unit, state class and enum options), never readings. CLM answers `watch`, the entities its enabled workflows use; `forward_states` sends only those (`entities.watched`), and an entity appearing or disappearing wakes the exchange. `publish_catalogue` runs every `HEARTBEAT` (300 s) and early on `entity_registry_updated`, `automation_reloaded`, a subentry change or a CLM `sync`, taking a burst as one after two seconds. What was exchanged lives in `Runtime.exchange` (memory only). Tests: `test_entities.py` (traits, unchanged offer and states not resent, the repeated exchange on `need`, watch list), `test_bridge_states.py` (only watched changes forwarded, appearing entity wakes the exchange); 75 component tests pass. Not run against a live Home Assistant.

## Approvals in the card and Run CLM item — 0.4.0

Requests are answered in the card's Approvals tab, which is CLM's embedded workspace; `repairs.py` and the Repairs strings were removed, and `entities._clear_repairs` deletes any `access_*` issue left by 0.3.0. In `entities.sync` a `pending` grant fires `clm_access_requested` once per start (`Runtime.asked`); a `confirming` grant (the person allowed it) is checked with `permitted` and answered through `entities/decision`, adding the subentry when allowed; either status for an entity the account cannot use is refused. `items.py` owns `clm.run_item`: `refresh` reads `capabilities` and up to 500 catalogue items per linked account (from `bridge.publish_catalogue`, every ten minutes and when the set of links changes), `describe` sets the service description with select options (`kind:id` with label and kind; action id with label; custom values allowed), and `run` sends `commands` as `native_actions.action_account` picks the account. Personal items are never listed. Tests: `test_entities.py`, `test_items.py`, `test_metadata.py`, `test_services.py`.

## Entity access and two-way requests — 0.3.0

`entities.py` owns the offer (`catalogue`, names only, filtered by the linked user's `read`/`control` permission), state snapshots (scalar attributes and short number lists, at most 4 KB), the exchange with CLM (`sync`), Repairs requests (`ask`), answers (`answer`), administrator offers (`offer`) and service calls (`control`, checked against the grant, the domain's services, the user's control permission and bounded light options). Allowed grants are config subentries of type `access` (title "entity · user", `unique_id` = CLM grant ID); `refresh` rebuilds the per-account index on every subentry change, and the entry's update listener wakes the exchange so a deletion reaches CLM at once. A grant just allowed counts as held only until its subentry exists. `repairs.py` owns the administrator-only Allow/Refuse fix flow; an aborted answer leaves the item in place. `config_flow.py` owns the "Share with CLM" subentry flow. HA keeps issues across restarts as inactive, so `ask` re-creates an inactive item and fires `clm_access_requested` only the first time.

`bridge.py` runs the exchange after each 30-second catalogue pass or a wake-up (CLM `sync` dispatch, subentry change, new link), forwards allowed state changes at most once a second per account, and routes dispatch kinds `sync`, `entity` and `automation`. Automations now also need a grant, both for runs and for forwarded starts. The first exchange per account adopts CLM's carried-over automation approvals (`grants_migrated` in entry data). Pending bindings (`pending` from `bindings/list`) are never run; `clm.run_workflow` explains that CLM must allow them first.

Verification: 68 component tests; a real Home Assistant Core 2026.10.0 with this component against CLM on a real port passed 34 of 34 live checks in both directions, and HA's own Repairs list, Allow/Refuse dialog and Shared with CLM entries were checked at 1440 and 390 px. The live pass found and fixed two defects: a revocation within a minute of allowing was not reported, and pending requests vanished from Repairs after an HA restart.
