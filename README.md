# CLM Companion

Native Home Assistant integration, maintained separately from CLM and its
[Lovelace card](https://github.com/edememinov/clm-ha-lovelace).
Baseline: Core 2026.10.0, Python 3.14.2 or later. Install `custom_components/clm`
in the HA configuration directory, restart, then add CLM in Devices & services.
Enable CLM authentication and grant Webhooks view/use plus the intended feature
permissions to each linked account. Configure the server and browser URLs as described below. Install the independent
CLM Lovelace card and link each user's own account from that card.

## Connection addresses

- **CLM server URL:** the backend base URL reached by HA, without `/api`.
  On a shared Docker network, this can be `http://clm-api:8080` (use your actual
  service name and container port). The browser does not use this address.
- **CLM web app URL (online / default):** where your browser opens CLM, such as
  `https://clm.example.com`. Used for account approval and the embedded workspace.
  A local-only setup can use its local web URL here.
- **CLM web app URL (local, optional):** a browser-reachable alternative, such as
  `http://192.168.1.20:4200`. Docker service names generally cannot be used here.
- **Additional Home Assistant browser URLs (optional):** normally empty. HA's
  internal, external and cloud URLs are discovered automatically. Add other exact
  browser addresses only for this same HA instance. These bound embedded account
  authorization to approved HA addresses. All addresses have native field help.

Use HTTPS online. Explicit HTTP addresses are supported for trusted local/Docker
networks; HTTPS is never silently downgraded. An HTTPS dashboard requires an HTTPS
CLM workspace. Auto mode selects the local workspace at HA's detected internal
origin, otherwise the default. The card editor's **CLM web address** selector can
override this with Local or Online / default. Pairing and embedding use the same
selected address; authority never comes from a browser-supplied workspace URL.

Update the CLM backend, integration and card together for multiple addresses.
Existing single-address entries continue to work. Use the integration's
**Reconfigure** action to add local access or refresh HA addresses after changing
HA network settings; account links and approved bindings are preserved. Changing
the backend itself requires a separate connection.

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
