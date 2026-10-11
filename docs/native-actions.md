# HA-owned native Windows actions

The integration automatically registers `clm.start_session`, `clm.open_selkies`,
`clm.open_clm` and `clm.list_devices` in HA's action picker. HA owns the trigger,
conditions and automation. These actions require no saved CLM workflow or MQTT
command entity. Existing MQTT discovery and entities continue to work.

Update the CLM backend and install the updated Windows worker plugin. Generate
and run its Windows setup again: it supplies the current worker files, backend
address, web app address, protected credential, device identity and logon task.
The Windows user must be logged in. The worker makes outbound requests; HA
does not execute PowerShell remotely. It uses the same device name as CLM's
Devices screen. `clm.list_devices` returns those names, IDs and worker status.

Link your CLM account from the card. Grant Webhooks view/use and Sessions view,
plus Sessions start for launching presets. The automation supplies the linked
account's current PIN; the HA card does not need Personal Mode. CLM checks the
PIN and account permissions on every call and selects only owned active Selkies
sessions. Interactive users cannot borrow another user's link. With multiple
linked accounts, unattended actions must specify `account_user_id` explicitly.

Add this action to an automation with your chosen HA trigger:

```yaml
action: clm.start_session
data:
  config_entry_id: YOUR_CLM_CONNECTION_ID
  device: Living room PC
  pin: !secret clm_personal_pin
  preset_name: Sample
  incognito: true
```

`preset_name` is an exact, unique preset name. This starts that preset and opens
its newest active Selkies viewer on the selected online worker. It opens the
viewer directly without first opening CLM. An ambiguous preset name is rejected.

To open already-running viewers, use `clm.open_selkies` with `session_name:
Sample` and `match: all` or `match: latest`, plus the same connection, device,
PIN and incognito fields. The name is a case-insensitive substring; latest uses
the session creation time. There is a maximum of 20 viewer windows per action.
`clm.open_clm` uses the same common fields and the web app address saved by the
worker's generated setup. Chrome, Edge, Firefox and Brave are supported.

PINs are deliberate HA action inputs and can appear in HA traces or be read by
HA operators. `!secret` avoids placing the literal value in automation YAML;
it does not hide it from runtime traces. CLM does not persist the supplied PIN
or include it in worker commands, entities or responses.

Actions acknowledge acceptance, not browser load completion. Delivery is
reserved once before opening a browser. A lost acknowledgement remains unknown
and is not automatically retried, preventing duplicate windows/session starts.
Offline workers are rejected before launching a session. If launch fails after
starting a session, inspect Sessions before deliberately running it again.
