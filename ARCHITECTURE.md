# Integration architecture

This repository owns the native Home Assistant adapter. It is independent of
the CLM application and card repositories. Supported API baseline: Core 2026.10.0.

Config flows use ConfigFlow and native selectors. Private WebSocket commands
derive identity exclusively from connection.user. Service actions derive identity
from call.context, then require current active HA users and individual CLM links.
HA administration grants no CLM account access. No personal content is published
to entities, state, recorder, service descriptions or events.

The client uses verified HTTPS with bounded requests and redacted failures.
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

Reference documentation:

- https://developers.home-assistant.io/docs/auth_permissions/
- https://developers.home-assistant.io/docs/core/integration/config_flow/
- https://developers.home-assistant.io/docs/dev_101_services/
- https://developers.home-assistant.io/docs/frontend/data/
- https://developers.home-assistant.io/docs/device_automation_trigger/
- https://developers.home-assistant.io/docs/automations/

The last interface is provisional and is excluded from implementation.
