"""Allow or refuse CLM's request to use an entity, from Settings > Repairs."""

from homeassistant.components.repairs import RepairsFlow
from homeassistant.exceptions import Unauthorized

from .client import CompanionError
from .entities import answer, label_of


class AccessRequestFlow(RepairsFlow):
    """Repairs flows are administrator-only; the answer is sent to CLM before the item closes."""

    def __init__(self, data: dict):
        self._request = data

    async def async_step_init(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self._request.get("entry_id", ""))
        user = await self.hass.auth.async_get_user(self._request.get("actor", ""))
        return self.async_show_menu(step_id="init", menu_options=["allow", "deny"], description_placeholders={
            "entity": label_of(self.hass, self._request.get("entity", ""), self._request.get("label")), "entity_id": self._request.get("entity", ""),
            "account": user.name if user and user.name else "CLM account", "connection": entry.title if entry else "CLM",
        })

    async def async_step_allow(self, user_input=None):
        return await self._answer(True)

    async def async_step_deny(self, user_input=None):
        return await self._answer(False)

    async def _answer(self, allow: bool):
        from . import Runtime
        entry = self.hass.config_entries.async_get_entry(self._request.get("entry_id", ""))
        if entry is None or not isinstance(entry.runtime_data, Runtime):
            return self.async_abort(reason="unavailable")
        try:
            await answer(self.hass, entry, self._request["actor"], self._request["grant"], self._request["entity"], allow)
        except Unauthorized:
            return self.async_abort(reason="not_permitted")
        except CompanionError:
            # The item stays in Repairs, so it can be answered again once CLM is reachable.
            return self.async_abort(reason="cannot_connect")
        return self.async_create_entry(data={})


async def async_create_fix_flow(hass, issue_id, data):
    return AccessRequestFlow(dict(data or {}))
