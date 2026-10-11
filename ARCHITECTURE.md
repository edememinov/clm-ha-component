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
