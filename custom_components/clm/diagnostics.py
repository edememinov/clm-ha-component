"""Diagnostics contain compatibility and adapter status only."""

async def async_get_config_entry_diagnostics(hass, entry):
    return {"api_version": 1, "configured": True, "loaded": entry.runtime_data is not None}
