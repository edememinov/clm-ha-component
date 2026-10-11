# Home Assistant: start sessions and open Windows viewers

Home Assistant owns the automation and its trigger. The CLM integration supplies actions that start a preset or open browser windows on a Windows worker. Supply the device, Personal Mode PIN, preset/session name and incognito setting in the HA action. The HA card can remain outside Personal Mode.

## Install and find the feature

1. Update the CLM backend and frontend deployments. Updating HACS alone does not update those containers.
2. Install CLM integration **0.2.0 or later** and Lovelace card **0.1.4 or later** through HACS, restart HA for the integration update and reload its browser UI for the card update. Card 0.1.4 adds the visible action/setup guide and worker status check; the actions themselves are provided by integration 0.2.0. Link your own CLM account through the card. Use the same CLM account that owns the personal presets and sessions.
3. Import the current Windows worker UI package into CLM through its plugin uploader. The package is distributed separately from the HA integration and card: HACS does not install it or its Windows files. Keep it non-personal so its settings remain accessible when Personal Mode is locked. If no package was supplied, this installation step is still outstanding.
4. Open **Plugin tools → Window visibility** in CLM's navbar. Expand **Windows setup**, select the existing device (or set up another device), and download Windows setup. Use backend and frontend addresses that this Windows PC can reach. Run the generated script as the Windows user who opens the browser. It installs the worker and its interactive-user logon task. On an existing device, setup replaces the old worker and task while preserving its identity and state.
5. The Windows user must be logged in and the worker running. Choose the device name shown in CLM **Devices**, or use its stable device ID. The worker uses that same name; do not invent a second worker name.
6. In HA, open your automation, then **Then do → Add action** and search for **CLM**. You can also test the actions in HA's **Actions** tool (called **Developer tools → Actions** on some versions). These actions are automatically registered; they are not MQTT buttons or new dashboard entities.

| HA action name | YAML action | Purpose |
| --- | --- | --- |
| Start session and open Selkies | `clm.start_session` | Start the uniquely named preset and open its newest active Selkies viewer on the chosen PC |
| Open matching Selkies viewers | `clm.open_selkies` | Open existing sessions matching a name substring; choose all or most recent |
| Open CLM window | `clm.open_clm` | Open the worker's configured CLM frontend address |
| List CLM Windows workers | `clm.list_devices` | Return shared device names, IDs, worker versions and online/offline status |

The per-type and per-session Window visibility switches control hide/show controls and the Remote button. They do not enable or disable these HA launch actions. Starting a session and opening a viewer needs no saved CLM workflow, visibility rule, HASS.Agent command or MQTT entity. Existing MQTT entities continue to work.

## Set it up entirely in the visual automation builder

No YAML file needs editing. Once the worker is installed and your account is linked:

1. In HA, open **Settings > Automations & scenes**, create or edit your automation and select the button press under **When** using HA's existing trigger editor.
2. Under **Then do**, select **Add action**, search **CLM**, and choose **Start session and open Selkies**.
3. Select **CLM connection**. HA saves the connection reference automatically; you do not need to find or paste a connection ID.
4. Enter **CLM device** using its shared Devices name. The Lovelace card's **HA automation actions and setup > Check Windows workers** button shows the names and current online/offline status.
5. Enter the linked account's current **Personal Mode PIN** in the password field. It stays part of the HA action and authorizes each execution; neither the card nor HA needs an unlocked CLM Personal Mode session.
6. Enter the exact **Preset name**, and set **Incognito window** on for a private window or off for a normal window.
7. If several HA accounts are linked, expand the advanced **Linked HA account** field and enter the intended HA user ID. With one linked account, leave it empty.
8. Save the automation. Use the action's **Run action** option to test; this starts a real session and opens its viewer on the chosen PC. Your normal button trigger then runs the same action.

To open existing viewers, choose **Open matching Selkies viewers** instead, fill in **Session name contains**, and choose **Most recently started match** or **All matches** under **Matching sessions**. To open CLM itself, choose **Open CLM window**. Both use the same connection/device/PIN/incognito controls.

The Lovelace card also shows whether each action is registered in the current HA instance. Expand **HA automation actions and setup** above its workspace. A registered action still needs an installed, online worker; use **Check Windows workers** to verify that prerequisite. Hide/show switches in CLM do not control launch actions.

## Optional YAML reference

The examples below are for people who already manage HA automations in YAML. They are not required for setup or execution from the graphical editor. In the graphical editor, simply enter the PIN in its password field; do not enter `!secret` there.

### Connection, account and PIN

Select the CLM connection in the HA action editor first. Switch that action to YAML to get its real `config_entry_id`; replace `YOUR_CLM_CONNECTION_ID` below with that value. It is HA's CLM connection ID, not a device ID or CLM account ID.

Grant the linked CLM account Webhooks view/use and Sessions view, plus Sessions start to start presets. The worker's generated API credential also needs the plugin and session permissions; follow its Windows setup options for personal-window reporting. Worker token Personal Mode and the per-call HA PIN are separate: the HA PIN authorizes the launch; it is never saved in the worker configuration or sent to Windows.

If just one HA account is linked to this connection, unattended actions use that link. With multiple linked accounts, supply `account_user_id` with the **HA user ID** whose CLM link should execute the automation. An interactive caller may use only their own link. The automation always acts with that linked CLM account's permissions and ownership.

In HA's `secrets.yaml`, define the current PIN as a quoted string so leading zeros survive:

```yaml
clm_personal_pin: "0123"
```

Replace the example with your PIN. `!secret` belongs in YAML; in the visual action editor, enter the PIN directly or edit the action's YAML. HA configuration and traces can contain this value. CLM checks it for each call and does not persist or forward it to the worker.

## Start a preset and open its viewer

Keep your existing button trigger and conditions in HA. Add the following to its `actions` list. Zigbee configuration remains entirely in HA.

```yaml
actions:
  - action: clm.start_session
    data:
      config_entry_id: YOUR_CLM_CONNECTION_ID
      device: Living room PC
      pin: !secret clm_personal_pin
      preset_name: Sample
      incognito: true
mode: single
```

Change `Sample` to the exact preset name, ignoring case. Names must be unique among accessible presets. `incognito: false` opens a normal window. The viewer opens directly on that PC without opening CLM first. The chosen preset must produce a Selkies session with a browser viewer URL reachable from the PC. The HA automation and dashboard do not need Personal Mode to remain unlocked.

## Open existing matching viewers

```yaml
actions:
  - action: clm.open_selkies
    data:
      config_entry_id: YOUR_CLM_CONNECTION_ID
      device: Living room PC
      pin: !secret clm_personal_pin
      session_name: Sample
      match: latest
      incognito: false
mode: single
```

`session_name` is a case-insensitive substring of the session name. `match: latest` picks the most recently created matching active owned Selkies session. Change it to `match: all` to open every matching viewer, up to 20. This action opens existing sessions; it does not start a preset. No matches is an error, not permission to start another session.

## Open CLM itself

```yaml
actions:
  - action: clm.open_clm
    data:
      config_entry_id: YOUR_CLM_CONNECTION_ID
      device: Living room PC
      pin: !secret clm_personal_pin
      incognito: true
```

The frontend address comes from that device's generated setup. Regenerate and run setup if that address changes. Normal/private launches support Chrome, Edge, Firefox and Brave; a local `browserPath` override can select the browser. Opening CLM does not automatically sign in to its web app.

## Check device online/offline status

Use this in a script or automation, or run List CLM Windows workers in HA's Actions tool:

```yaml
actions:
  - action: clm.list_devices
    data:
      config_entry_id: YOUR_CLM_CONNECTION_ID
    response_variable: clm_workers
```

The response is `devices`, with each row containing `id`, `name`, `online` and `version`. Devices appear after their first worker report; an uninstalled worker is not listed. A worker is online when its last report is within 15 seconds. The action returns status data; this release does not create a worker binary sensor automatically. You can use `clm_workers.devices` in later HA template conditions. This listing needs no PIN and includes no personal session names.

## If it is missing or fails

- No `clm.start_session` in HA's action picker: check the installed integration version, restart HA, reload the browser, and inspect HA logs for a CLM setup/import error. The Lovelace card update alone does not install integration actions.
- No Plugin tools page: deploy the updated CLM frontend/backend, import the worker UI package, enable it and grant plugin access. The worker UI and Windows installer are separate from the HACS packages.
- Empty worker list: run generated Windows setup and check its scheduled task, API credential and PC-to-backend connectivity. An open CLM tab alone is not a worker heartbeat.
- Offline/ambiguous device: choose a unique shared Devices name or its stable ID. Offline workers are rejected before a session is launched.
- Rejected launch: check the linked account, current PIN, permissions, unique preset name, ownership and active Selkies viewer. The generic HA error alone does not identify which prerequisite failed.
- Accepted but no window: acceptance confirms queueing, not browser load. Check the Windows user's interactive login, browser path, worker and viewer URL reachability. HA does not remotely execute PowerShell; the worker polls CLM using outbound requests.
- Do not automatically retry an unknown result. Worker delivery is reserved before opening a browser to prevent duplicate windows. If launch fails after starting the session, inspect CLM Sessions before deliberately retrying.

The YAML follows Home Assistant's [action and response-variable documentation](https://www.home-assistant.io/docs/scripts/perform-actions/) and [secrets documentation](https://www.home-assistant.io/docs/configuration/secrets/).
