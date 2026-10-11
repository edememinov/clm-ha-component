# Run CLM automations from Home Assistant

Use **Run CLM workflow** for a button or an action you execute as a signed-in HA user. Use the workflow's **MQTT trigger** for a schedule or sensor event. CLM executes the workflow on its backend; a browser session is needed only when one of its actions specifically targets a browser or CLM window.

## Connect and approve a workflow

1. Install component version **0.1.3 or later**, restart HA, and add CLM under **Settings → Devices & services**. Configure the backend URL HA can reach and the web app URL your browser can reach. See the main [README](../../README.md#connection-addresses).
2. Add the CLM Lovelace card and link your own CLM account. Approve the account challenge in CLM. The installation connection alone does not grant access to workflows.
3. Give the linked CLM account **Webhooks view**, **Webhooks use**, and permissions for every action the workflow performs.
4. In CLM, create and enable the workflow under **Tools & Extensions → Automations**. For the native component path, choose a non-personal workflow with fixed actions. Nested automation actions and actions that unlock Personal Mode are not exposed by the companion catalogue.
5. In the card's embedded CLM workspace, search for the workflow, select **Run automation**, then choose **Run action** to test it. Choose **Approve companion binding** to authorize a reusable call for your linked account. Each binding has its own ID and expiry; it is not the workflow's ID.

The component invokes the existing approved binding; it does not need an API key, webhook signing secret or MQTT connection for this path. The backend checks current permissions and the enabled state on every call.

## Find the approved binding

In **Developer tools → Actions**, choose **CLM: List approved CLM bindings** (`clm.list_bindings`). Select your CLM connection and leave **Workflows only** on. Perform the action and inspect its response:

```yaml
action: clm.list_bindings
data:
  config_entry_id: YOUR_CLM_CONFIG_ENTRY_ID
  workflows_only: true
```

The response contains a `bindings` list with each binding's `id`, `operation` (`automation.run`) and `expires` (Unix seconds). It lists only your own non-personal approvals. If needed, approve one workflow at a time and compare the list before/after to identify its new ID. Connection IDs are also present in your CLM card configuration as `config_entry_id`.

## Run it from Actions or a dashboard button

Choose **CLM: Run CLM workflow** (`clm.run_workflow`), select the CLM connection, and paste the approved binding ID. A call in YAML is:

```yaml
action: clm.run_workflow
data:
  config_entry_id: YOUR_CLM_CONFIG_ENTRY_ID
  binding: YOUR_APPROVED_BINDING_ID
```

A standard dashboard button uses the same action:

```yaml
type: button
name: Run evening workflow
icon: mdi:play-network
tap_action:
  action: perform-action
  perform_action: clm.run_workflow
  data:
    config_entry_id: YOUR_CLM_CONFIG_ENTRY_ID
    binding: YOUR_APPROVED_BINDING_ID
```

You can also put that action in an HA script you execute interactively. It runs as the authenticated HA caller's linked CLM account. Another HA user needs their own account link and approved binding. Do not put an API key or PIN in this action.

The optional action response contains `id`, `status`, `code` and `duplicate`. A failed workflow raises an HA action error. An `accepted` response means execution was accepted, not that every asynchronous effect has finished. Requests are not automatically retried after a network failure.

## Run on a schedule or sensor event over MQTT

Scheduled HA automations normally have no authenticated user context. `clm.run_workflow` deliberately does not infer an account for those calls. Use the MQTT trigger with an explicitly authorized API key instead:

1. In CLM **Access → API keys**, create a key for the intended CLM account. Grant **MQTT connect/read/send**, **Webhooks use**, and the workflow's action permissions.
2. In the workflow editor, add an **MQTT** trigger and save. Keep the workflow enabled.
3. Open **MQTT topic & payload** on its row. Copy the exact run/result topics and choose the Home Assistant example. The key's account determines the topic prefix; do not copy another user's prefix.
4. Connect HA's MQTT integration to CLM's broker using any username and that API key as the password. If HA already uses another broker, keep it and use CLM's MQTT screen to generate a bridge or relay configuration. Configure broker credentials there, not inside automation payloads.
5. Add an **MQTT: Publish** action to an existing HA automation. For an unsigned fixed workflow, the action is:

```yaml
actions:
  - action: mqtt.publish
    data:
      topic: clm/YOUR_ACCOUNT_KEY/automations/YOUR_WORKFLOW_ID/run
      payload: '{}'
      qos: 1
      retain: false
```

Here `YOUR_WORKFLOW_ID` is the workflow ID from its MQTT guide, not a companion binding ID. Use the copied full topic rather than reconstructing it by hand. Keep your existing HA triggers and conditions. Publication confirms the message was sent; inspect the result topic shown in CLM and **Recent runs** to check the workflow outcome.

If **Only accept signed requests** is enabled, use CLM's generated signed MQTT example; plain `{}` is refused. For unattended calls, use a non-personal workflow. PIN-required workflows require a request PIN; never store a personal PIN in HA action data, traces or secrets. Enabled MQTT workflows may also appear as Home Assistant discovery controls when discovery/bridge forwarding is configured.

## HTTP webhook alternative

Enable and save a **Webhook** trigger, then open **Webhook address & code** on the workflow. Choose its Home Assistant REST example. Use the generated POST address, a bearer API key stored in HA secrets, and the generated signing setup when signatures are required. Calls execute the same saved workflow. This REST path uses HA's own REST action support rather than the companion component.

## Which action should I choose?

| Action | What it runs |
|---|---|
| `clm.run_workflow` | An approved non-personal workflow saved in CLM, for an authenticated linked user. |
| `clm.invoke` | An approved CLM binding, including existing leaf actions. |
| `clm.run_automation` | An existing HA `automation.*` entity, with conditions enabled and the component's bounded binding checks. It does not run a CLM workflow. |
| `mqtt.publish` | The saved workflow's MQTT trigger; suitable for unattended HA schedules and sensor events. |

The existing **Add approved action to automation** handoff is intended for permitted leaf bindings. A CLM workflow binding is not eligible for the component's outbound HA automation bridge, which excludes recursive workflow calls.

## Troubleshooting

- **No workflow bindings:** approve **Run automation** in the card workspace while Personal Mode is off; verify your account link and permissions. Personal, nested or otherwise ineligible workflows may not appear.
- **Unauthorized:** check that you are signed in to HA, linked to the correct CLM account and using that account's binding. A workflow ID or another user's binding does not qualify. Anonymous scheduled calls must use MQTT.
- **Expired or revoked binding:** approve a new binding and update the button. Approvals currently expire after 30 days; account revocation invalidates them earlier.
- **Workflow failed / code 409:** check its enabled state, configured trigger and **Recent runs**. Do not automatically repeat a call whose outcome is unknown.
- **MQTT publishes but nothing runs:** check the broker/bridge, account prefix, exact run topic, saved MQTT trigger, API key permissions and signing. Look for the answer on the matching result topic.
- **Action missing:** update the component, restart HA and reload your browser to refresh native action translations.

Native action and YAML syntax references: [HA integration service actions](https://developers.home-assistant.io/docs/dev_101_services/), [dashboard actions](https://www.home-assistant.io/dashboards/actions/), [MQTT publish](https://www.home-assistant.io/integrations/mqtt/#action-mqttpublish).
