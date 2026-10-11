"""Native installation setup; account authority is linked separately."""

import probatio as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .addresses import discover_origins
from .client import CompanionClient, CompanionError, web_origin
from .const import DOMAIN


class ClmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the trusted transport, without an owner's execution credential."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        return await self._configure(user_input)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        return await self._configure(user_input, entry)

    async def _configure(self, user_input, entry=None):
        errors = {}
        discovered, internal = discover_origins(self.hass)
        if user_input is not None:
            try:
                api = web_origin(user_input["api_origin"], allow_http=True)
                workspace = web_origin(user_input["workspace_origin"], allow_http=True)
                local = user_input.get("workspace_local_origin", "")
                local = web_origin(local, allow_http=True) if local else None
                extra = [web_origin(value, allow_http=True) for value in user_input.get("extra_ha_origins", [])]
                audiences = list(dict.fromkeys(discovered + extra))
                if not audiences:
                    errors["base"] = "no_ha_url"
                elif len(audiences) > 10:
                    errors["base"] = "too_many_origins"
                elif entry and api != entry.data["api_origin"]:
                    errors["api_origin"] = "backend_changed"
                else:
                    client = CompanionClient(async_get_clientsession(self.hass), api)
                    if entry:
                        installation = await client.request("installations/origins", {"audiences": audiences}, entry.data["auth_token"])
                    else:
                        if any(value.data.get("api_origin") == api for value in self._async_current_entries()):
                            return self.async_abort(reason="already_configured")
                        await self.async_set_unique_id(api)
                        self._abort_if_unique_id_configured()
                        installation = await client.request("installations", {"audience": audiences[0], "audiences": audiences})
                    if not isinstance(installation, dict):
                        raise CompanionError()
                    if installation.get("version") != 1 or installation.get("audiences") != audiences:
                        return self.async_abort(reason="unsupported_version")
                    data = {
                        "api_origin": api, "workspace_origin": workspace,
                        "workspace_local_origin": local, "ha_origin": audiences[0],
                        "ha_origins": audiences, "internal_ha_origin": internal,
                        "extra_ha_origins": extra,
                    }
                    if entry:
                        return self.async_update_reload_and_abort(entry, data_updates=data)
                    data.update(installation=installation["installation"], auth_token=installation["credential"])
                    return self.async_create_entry(title=f"CLM ({api})", data=data)
            except ValueError:
                errors["base"] = "invalid_origin"
            except CompanionError:
                errors["base"] = "cannot_connect"
        defaults = dict(entry.data) if entry else {}
        if entry and "ha_origins" not in defaults:
            defaults["extra_ha_origins"] = [defaults["ha_origin"]]
        if user_input:
            defaults.update(user_input)
        text = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))
        schema = vol.Schema({
            vol.Required("api_origin"): text,
            vol.Required("workspace_origin"): text,
            vol.Optional("workspace_local_origin"): text,
            vol.Optional("extra_ha_origins"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL, multiple=True)),
        })
        return self.async_show_form(
            step_id="reconfigure" if entry else "user", errors=errors,
            description_placeholders={"ha_urls": ", ".join(discovered) or "None detected; configure HA network URLs or add an address below."},
            data_schema=self.add_suggested_values_to_schema(schema, {key: value for key, value in defaults.items() if value is not None}),
        )
