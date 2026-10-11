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
Mixed HTTPS/HTTP embedding is rejected, with an HTTPS default fallback in auto
mode. Existing entries retain their one approved audience until reconfigured.
Reconfiguration updates the remote approved origins using installation transport
authority and reloads HA without replacing account storage or installation IDs.

Reference documentation:

- https://developers.home-assistant.io/docs/auth_permissions/
- https://developers.home-assistant.io/docs/core/integration/config_flow/
- https://developers.home-assistant.io/docs/dev_101_services/
- https://developers.home-assistant.io/docs/frontend/data/
- https://developers.home-assistant.io/docs/device_automation_trigger/
- https://developers.home-assistant.io/docs/automations/

The last interface is provisional and is excluded from implementation.
