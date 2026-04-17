"""Support for Noonlight text entities."""
import logging
from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Noonlight text entities from a config entry."""
    noonlight_integration = hass.data[DOMAIN][config_entry.entry_id]
    
    async_add_entities([NoonlightEventText(noonlight_integration)])

class NoonlightEventText(TextEntity):
    """Text entity to input custom event messages."""
    
    def __init__(self, noonlight_integration):
        """Initialize the text entity."""
        self.noonlight = noonlight_integration
        self._attr_name = "Event Message"
        self._attr_unique_id = f"event_message_{self.noonlight.config.get('id', 'default')}"
        self._attr_native_value = ""
        self._attr_icon = "mdi:square-edit-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.noonlight.config.get('id', 'default'))},
            "name": "Noonlight Alarm",
            "manufacturer": "Noonlight",
            "model": "V2 Dispatch",
        }
        # Initialize integration state
        self.noonlight.event_text_input = self._attr_native_value

    async def async_set_value(self, value: str) -> None:
        """Change the text value."""
        self._attr_native_value = value
        self.noonlight.event_text_input = value
        self.async_write_ha_state()
