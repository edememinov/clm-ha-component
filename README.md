# CLM Companion

Native Home Assistant integration, maintained separately from CLM and its
[Lovelace card](https://github.com/edememinov/clm-ha-lovelace).
Baseline: Core 2026.10.0, Python 3.14.2 or later. Install `custom_components/clm`
in the HA configuration directory, restart, then add CLM in Devices & services.
Enable CLM authentication and grant Webhooks view/use plus the intended feature
permissions to each linked account. Configure the API, workspace and HA HTTPS origins. Install the independent
CLM Lovelace card and link each user's own account from that card.

No installation token can browse or execute CLM items. No administrator fallback
exists. Account links use an expiring challenge approved in signed-in CLM.
Personal PINs remain in the embedded view's memory and never enter HA actions,
traces, entities or storage. Trusted HA operators can access its credential storage.
Removing a connection attempts remote revocation and removes local credentials;
offline removal requires revoking the link in CLM as well.

`clm.invoke` executes an opaque approved binding. Anonymous invocations are denied
unless their server-created parent context belongs to authenticated integration
ingress and the binding is in its bounded approved scope. `clm.run_automation`
checks current user permissions and enabled state, preserves conditions and uses
HA's existing automation engine. Personal unattended bindings and deeper anonymous
context ancestry are denied. No provisional automation API is used.

Install `requirements-test.txt`, then run `python -m unittest discover -s tests`. Platform adapter tests additionally
require the pinned HA dependencies. Architecture and interface references are
recorded in [ARCHITECTURE.md](ARCHITECTURE.md).

The outbound bridge advertises only permitted enabled existing automations,
stores definition fingerprints, reserves each delivery once and rechecks its
fingerprint before invoking the native engine with conditions enabled. Edited
definitions require CLM approval again. HA's trigger service does not return a
reliable completion/condition result: its acknowledgement remains **accepted**;
lost acknowledgements remain **unknown**. It never claims runtime completion.
Bridge-authorized actions are limited to approved leaf bindings to prevent cycles.
