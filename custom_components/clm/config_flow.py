"""Native installation setup; account authority is linked separately."""

import probatio as vol

from homeassistant import config_entries
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .addresses import discover_origins
from .client import CompanionClient, CompanionError, web_origin
from .const import DOMAIN


class ClmConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure the trusted transport, without an owner's execution credential."""

    VERSION = 1

    @classmethod
    def async_get_supported_subentry_types(cls, config_entry):
        """Each allowed entity is a subentry: listed, and revoked by deleting it, under Devices & services."""
        return {"access": AccessSubentryFlow}

    async def async_step_user(self, user_input=None):
        return await self._configure(user_input)

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        return await self._configure(user_input, entry)

    async def _configure(self, user_input, entry=None):
        errors = {}
        discovered, internal = discover_origins(self.hass)
        values = {}
        extra = []
        if user_input is not None:
            for field in ("api_origin", "workspace_origin", "workspace_local_origin", "workspace_local_fallback_origin"):
                value = user_input.get(field, "")
                if not value and field not in ("api_origin", "workspace_origin"):
                    values[field] = None
                    continue
                if not isinstance(value, str) or "://" not in value:
                    errors[field] = "missing_scheme"
                    continue
                try:
                    values[field] = web_origin(value, allow_http=True)
                except ValueError:
                    errors[field] = "invalid_url"
            stored_extra = entry.data.get("extra_ha_origins", [entry.data["ha_origin"]] if "ha_origins" not in entry.data else []) if entry else []
            for value in user_input.get("extra_ha_origins", stored_extra):
                try:
                    if not isinstance(value, str) or "://" not in value:
                        errors["extra_ha_origins"] = "missing_scheme"
                    else:
                        extra.append(web_origin(value, allow_http=True))
                except ValueError:
                    errors["extra_ha_origins"] = "invalid_url"
        if user_input is not None and not errors:
            try:
                api = values["api_origin"]
                workspace = values["workspace_origin"]
                local = values["workspace_local_origin"]
                audiences = list(dict.fromkeys(discovered + extra))
                if not audiences:
                    errors["base"] = "no_ha_url"
                elif len(audiences) > 10:
                    errors["base"] = "too_many_origins"
                elif entry and api != entry.data["api_origin"]:
                    errors["api_origin"] = "backend_changed"
                else:
                    client = CompanionClient(async_get_clientsession(self.hass), api)
                    await client.check_transport()
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
                        "workspace_local_fallback_origin": values["workspace_local_fallback_origin"],
                        "ha_origins": audiences, "internal_ha_origin": internal,
                        "extra_ha_origins": extra,
                    }
                    if entry:
                        return self.async_update_reload_and_abort(entry, data_updates=data)
                    data.update(installation=installation["installation"], auth_token=installation["credential"])
                    return self.async_create_entry(title=f"CLM ({api})", data=data)
            except CompanionError as error:
                errors["api_origin"] = error.reason
        defaults = dict(entry.data) if entry else {}
        if entry and "ha_origins" not in defaults:
            defaults["extra_ha_origins"] = [defaults["ha_origin"]]
        if user_input:
            defaults.update(user_input)
        text = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))
        fields = {
            vol.Required("api_origin"): text,
            vol.Required("workspace_origin"): text,
            vol.Optional("workspace_local_origin"): text,
            vol.Optional("workspace_local_fallback_origin"): text,
        }
        if getattr(self, "show_advanced_options", False) or not discovered or "extra_ha_origins" in errors:
            fields[vol.Optional("extra_ha_origins")] = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL, multiple=True))
        schema = vol.Schema(fields)
        return self.async_show_form(
            step_id="reconfigure" if entry else "user", errors=errors,
            description_placeholders={"ha_urls": ", ".join(discovered) or "None detected; configure HA network URLs or add an address below."},
            data_schema=self.add_suggested_values_to_schema(schema, {key: value for key, value in defaults.items() if value is not None}),
        )


class AccessSubentryFlow(config_entries.ConfigSubentryFlow):
    """An administrator sharing an entity with a linked CLM account without waiting to be asked."""

    async def async_step_user(self, user_input=None):
        from . import Runtime
        from .entities import DOMAINS, offer, label_of
        entry = self._get_entry()
        runtime = entry.runtime_data
        if not isinstance(runtime, Runtime):
            return self.async_abort(reason="unavailable")
        accounts = []
        for actor in list(runtime.links):
            user = await self.hass.auth.async_get_user(actor)
            if user is not None and user.is_active:
                accounts.append(selector.SelectOptionDict(value=actor, label=user.name or actor))
        if not accounts:
            return self.async_abort(reason="no_linked_accounts")
        errors = {}
        if user_input is not None:
            actor, entity = user_input["account"], user_input["entity"]
            try:
                grant = await offer(self.hass, entry, actor, entity)
            except Unauthorized:
                errors["entity"] = "not_permitted"
            except (CompanionError, KeyError):
                errors["base"] = "cannot_connect"
            else:
                user = await self.hass.auth.async_get_user(actor)
                return self.async_create_entry(title=f"{label_of(self.hass, entity)} · {user.name or 'CLM account'}",
                                               data={"grant": grant, "actor": actor, "entity": entity}, unique_id=grant)
        schema = vol.Schema({
            vol.Required("entity"): selector.EntitySelector(selector.EntitySelectorConfig(domain=[*DOMAINS, "automation"])),
            vol.Required("account", default=accounts[0]["value"]): selector.SelectSelector(selector.SelectSelectorConfig(options=accounts)),
        })
        return self.async_show_form(step_id="user", data_schema=self.add_suggested_values_to_schema(schema, user_input or {}), errors=errors)
