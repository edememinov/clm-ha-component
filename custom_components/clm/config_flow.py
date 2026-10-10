"""Native installation setup; account authority is linked separately."""

import probatio as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .client import CompanionClient, CompanionError, https_origin
from .const import DOMAIN


class ClmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the trusted transport, without an owner's execution credential."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                api = https_origin(user_input["api_origin"])
                workspace = https_origin(user_input["workspace_origin"])
                audience = https_origin(user_input["ha_origin"])
                await self.async_set_unique_id(f"{api}|{audience}")
                self._abort_if_unique_id_configured()
                client = CompanionClient(async_get_clientsession(self.hass), api)
                installation = await client.request("installations", {"audience": audience})
                if not isinstance(installation, dict):
                    raise CompanionError()
                if installation.get("version") != 1:
                    return self.async_abort(reason="unsupported_version")
                return self.async_create_entry(title=f"CLM ({api})", data={
                    "api_origin": api, "workspace_origin": workspace, "ha_origin": audience,
                    "installation": installation["installation"], "auth_token": installation["credential"],
                })
            except ValueError:
                errors["base"] = "invalid_origin"
            except CompanionError:
                errors["base"] = "cannot_connect"
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required("api_origin"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Required("workspace_origin"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
            vol.Required("ha_origin"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL)),
        }))
