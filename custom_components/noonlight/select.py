"""Support for Noonlight select entities."""
import logging
from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Noonlight select entities from a config entry."""
    noonlight_integration = hass.data[DOMAIN][config_entry.entry_id]
    
    async_add_entities([NoonlightServiceSelect(noonlight_integration)])

class NoonlightServiceSelect(SelectEntity):
    """Select entity to choose the emergency service type."""
    
    def __init__(self, noonlight_integration):
        """Initialize the select entity."""
        self.noonlight = noonlight_integration
        self._attr_name = "Alarm Type"
        self._attr_unique_id = f"alarm_type_{self.noonlight.config.get('id', 'default')}"
        self._attr_options = ["police", "fire", "medical", "other"]
        self._attr_current_option = "police"
        self._attr_icon = "mdi:bell-cog"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.noonlight.config.get('id', 'default'))},
            "name": "Noonlight Alarm",
            "manufacturer": "Noonlight",
            "model": "V2 Dispatch",
        }
        # Initialize integration state
        self.noonlight.selected_service = self._attr_current_option

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            self._attr_current_option = option
            self.noonlight.selected_service = option
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Invalid option selected: %s", option)
