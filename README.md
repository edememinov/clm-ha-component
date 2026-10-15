# CLM Companion

Native Home Assistant integration, maintained separately from CLM and its
[Lovelace card](https://github.com/edememinov/clm-ha-lovelace).
Baseline: Core 2026.10.0, Python 3.14.2 or later. Install `custom_components/clm`
in the HA configuration directory, restart, then add CLM in Devices & services.
Enable CLM authentication and grant Webhooks view/use plus the intended feature
permissions to each linked account. Configure the server and browser URLs as described below. Install the independent
CLM Lovelace card and link each user's own account from that card.

## Use HA devices in CLM

From version 0.3.0 the integration offers each linked account's lights, buttons (`button`, `input_button`) and sensors (`sensor`, `binary_sensor`) to CLM; from 0.6.0 also switches, input booleans, fans, covers, valves, locks, climate, humidifiers, water heaters, media players, vacuums, sirens, scenes, scripts, numbers, selects and text entities, by name only, filtered by what that Home Assistant user may use. CLM can use one only after you allow it in the CLM card:

1. In CLM, open **Companion connections**, select this connection and choose **Ask Home Assistant** for a device.
2. Open the CLM card's **Approvals** tab and choose **Allow** or **Refuse**. The integration then checks that your Home Assistant user may use the entity before it counts. It also fires `clm_access_requested` (`entity_id`, `account`, `config_entry_id`) when a request arrives, so an automation can notify your phone. From 0.4.0 requests no longer appear in Repairs.
3. Allowed entities are listed under the CLM integration in **Devices & services** as **Shared with CLM**. Delete an entry to revoke it; CLM stops using it at once. **Share with CLM** on the same page allows an entity without waiting for a request.

CLM workflows can then call every service of the entity's domain that CLM lists (for a light: on, off and toggle with brightness, brightness steps, colour temperature, colour, effect, flash and fade; for a switch: on, off and toggle; for a cover: open, close, stop and positions; for a media player: playback, volume, source and more), press buttons, and read states and attributes in conditions and state triggers. Each service accepts only its own options, and Home Assistant's own service schema checks their values; nothing can aim a call at another entity. The catalogue sends what each entity supports (its feature bits, effect and preset lists, number ranges, select options) so CLM offers only those. Allowed entities' state changes reach CLM within about a second. Service calls run as the linked Home Assistant user and are refused when that user may no longer control the entity.

Approving a Home Assistant automation in CLM asks in the card's Approvals tab too. Automations approved before 0.3.0 are carried over once and keep working.

Update the CLM backend together with the integration: an older backend does not offer these routes.

## Run CLM items from HA automations

From 0.4.0, choose **CLM: Run CLM item** (`clm.run_item`) in HA's **Add action** picker. Pick the CLM connection, then the **CLM item** and the **Action** by name. The lists show what your linked account may use in CLM (personal items are not listed) and refresh every ten minutes; reload the page after adding items in CLM. Every run uses the account's current CLM permissions. **Plugin inputs** and **CLM window** (advanced) carry values for actions that ask for them. With several linked accounts, unattended runs need **Linked HA account**.

```yaml
action: clm.run_item
data:
  config_entry_id: YOUR_CLM_CONFIG_ENTRY_ID
  item: game-preset:42
  operation: session.start
```

The card's workspace has no binding request any more: CLM items no longer need one. Existing bindings used with `clm.invoke` and `clm.run_workflow` keep working.

## Start sessions and open Windows windows from HA

For HA automations that launch presets or open Windows viewers directly, use
the automatically registered [native Windows actions](docs/native-actions.md).
These actions use a device, supplied PIN, preset/session name and incognito
setting. HA owns their trigger; no CLM workflow is required. Existing MQTT
discovery remains available.

Find **CLM: Start session and open Selkies**, **Open matching Selkies viewers**,
**Open CLM window**, and **List CLM Windows workers** in HA's **Add action**
picker. The [step-by-step setup and YAML guide](docs/native-actions.md) covers
the separately installed Windows worker, per-call PIN, shared device names,
normal/private launches, all/latest selection and worker online/offline status.
Updating HACS alone does not install or upgrade the Windows worker.

## Run CLM workflows

To start a CLM workflow when an existing HA automation fires, install component
**0.2.1 or later** and the matching updated CLM backend/frontend. Link your account,
approve that HA automation under **CLM Settings → Companion connections**, then
open the CLM workflow's **Triggers** section and select **Home Assistant automation**
and the approved automation. Save and enable the workflow. Its actions start when
HA starts that automation; no extra HA action, MQTT configuration or browser tab
is required. HA conditions still decide whether its automation starts.

This event path is immediate. The component also holds a bounded delivery request
open so CLM button commands wake it immediately, independently of the regular
exchange (every five minutes, and at once when an entity, an automation or an
access entry changes here). A slow automation does not block new delivery. Approving the
same configuration renews its stable binding instead of creating duplicate picker
items. Editing the HA definition requires approval again. Use non-personal CLM
workflows without nested workflow, external automation or Personal Mode actions.

For a dashboard button or an authenticated action, approve the workflow in the
CLM card workspace, use **List approved CLM bindings** to find its binding, then
call **Run CLM workflow** (`clm.run_workflow`). For schedules or sensor triggers,
use the workflow's MQTT trigger. These are distinct from `clm.run_automation`,
which runs an existing Home Assistant automation.

The [workflow execution README](docs/automation-execution/README.md) includes
setup, approval, native action and dashboard examples, unattended MQTT execution,
HTTP webhook alternatives and troubleshooting.

## Connection addresses

- **CLM backend URL:** the backend base URL reached by HA, without `/api`.
  On a shared Docker network, this can be `http://clm-api:8080` (use your actual
  service name and container port). The browser does not use this address.
- **CLM web app URL (default):** where your browser opens CLM, such as
  `https://clm.example.com`. Used for account approval and the embedded workspace.
  A local-only setup can use its local web URL here.
- **Local CLM web app URL (optional):** a browser-reachable alternative, such as
  `http://192.168.1.20:4200`. Docker service names generally cannot be used here.
- **Local IP fallback URL (optional):** a second browser-reachable frontend URL
  for devices that cannot resolve the local hostname, with the frontend port.
  HTTPS requires a certificate trusted by that device for that IP address.
- **Other Home Assistant URLs (advanced):** normally hidden and empty. Enable
  HA's advanced setup options to add overrides. HA's
  internal, external and cloud URLs are discovered automatically. Add other exact
  browser addresses only for this same HA instance. These bound embedded account
  authorization to approved HA addresses. All addresses have native field help.

Use HTTPS online. Explicit HTTP addresses are supported for trusted local/Docker
networks; HTTPS is never silently downgraded. An HTTPS dashboard requires an HTTPS
CLM workspace. Auto mode tries the local hostname, local IP fallback and default
URL from each browser, independently of the HA URL or server DNS. No cookies or
credentials are sent during reachability checks; frame authorization still
requires the exact configured origin and nonce. The card editor's **CLM web address** selector can
override this with Local or Online / default. Pairing and embedding use the same
selected address; authority never comes from a browser-supplied workspace URL.

Update the CLM backend, integration and card together for multiple addresses.
Existing single-address entries continue to work. Use the integration's
**Reconfigure** action to add local access or refresh HA addresses after changing
HA network settings; account links and approved bindings are preserved. Changing
the backend itself requires a separate connection.

Setup marks each incomplete/invalid URL at its own field. A read-only backend
compatibility probe explains when the running backend image is outdated, before
creating any installation state. HACS updates the integration and card; it does
not replace your CLM backend container. Rebuild/pull and recreate that container
using your normal deployment process after updating backend code.

After upgrading an integration, restart HA and reload the browser (or use a
fresh private window) so native form translations are refreshed. The web app URL
is a browser launch/embed destination, not a second server registration. For
consistent local/online access, a single HTTPS hostname with split DNS avoids
device-specific local hostname resolution and preserves certificate names.

## Releases

Pushes to `main` automatically validate the integration and publish a stable
GitHub release using the version in `custom_components/clm/manifest.json`.
Component changes require a version bump; the workflow rejects attempts to
replace an already published version. Documentation-only pushes and reruns do
not duplicate releases. A failed draft can be resumed for the same commit.
The workflow can also be started manually from GitHub Actions. No integration
archive or shared browser-extension package is built.

No installation token can browse or execute CLM items. No administrator fallback
exists. Account links use an expiring challenge approved in signed-in CLM.
PINs entered in the embedded view stay in that view's memory. Direct native
actions deliberately take a PIN from HA action data: HA operators and automation
traces may see it. Use `!secret` in YAML. CLM does not persist this PIN or send it
to the Windows worker. Trusted HA operators can access its credential storage.
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

## What is sent to CLM, and how often

Entities shared with CLM change CLM workflows the moment they change, without polling:

- Changes of the shared entities that an enabled CLM workflow uses are sent at once,
  at most once a second per account. CLM tells the integration which ones those are.
- Other shared entities' changes go with the regular exchange, every five minutes.
- The list of what could be shared, with each entity's abilities (a light's colour modes
  and white range, a sensor's class, unit and options), is sent only when it changed.
- After CLM restarts, it asks once for everything again.
