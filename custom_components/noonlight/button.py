"""Support for Noonlight buttons."""
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Noonlight buttons from a config entry."""
    noonlight_integration = hass.data[DOMAIN][config_entry.entry_id]
    
    async_add_entities([
        NoonlightCancelButton(noonlight_integration),
        NoonlightSendEventButton(noonlight_integration)
    ])

class NoonlightCancelButton(ButtonEntity):
    """Button to cancel an active Noonlight alarm."""
    
    def __init__(self, noonlight_integration):
        """Initialize the button."""
        self.noonlight = noonlight_integration
        self._attr_name = "Cancel Alarm"
        self._attr_unique_id = f"cancel_alarm_{self.noonlight.config.get('id', 'default')}"
        self._attr_icon = "mdi:alarm-off"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.noonlight.config.get('id', 'default'))},
            "name": "Noonlight Alarm",
            "manufacturer": "Noonlight",
            "model": "V2 Dispatch",
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        pin = getattr(self.noonlight, 'pin_input', None)
        await self.noonlight.cancel_alarm(pin=pin)

class NoonlightSendEventButton(ButtonEntity):
    """Button to send a custom event to an active Noonlight alarm."""
    
    def __init__(self, noonlight_integration):
        """Initialize the button."""
        self.noonlight = noonlight_integration
        self._attr_name = "Send Event"
        self._attr_unique_id = f"send_event_{self.noonlight.config.get('id', 'default')}"
        self._attr_icon = "mdi:message-arrow-right"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.noonlight.config.get('id', 'default'))},
            "name": "Noonlight Alarm",
            "manufacturer": "Noonlight",
            "model": "V2 Dispatch",
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        if self.noonlight._alarm is not None:
            alarm_id = self.noonlight._alarm.id
            event_text = getattr(self.noonlight, 'event_text_input', '')
            if not event_text:
                _LOGGER.warning("Cannot send empty event")
                return
            
            _LOGGER.debug("Sending event '%s' for alarm %s", event_text, alarm_id)
            
            from homeassistant.util import dt as dt_util
            event_body = [{
                "event_type": "alarm.device.activated_alarm",
                "event_time": dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "meta": {
                    "attribute": "description",
                    "value": event_text,
                    "label": event_text
                }
            }]
            self.noonlight.show_api_diagnostic("Send Event", self.noonlight.active_client, f"{self.noonlight.active_client.alarms_url}/{alarm_id}/events", event_body)
            await self.noonlight.active_client.create_event(id=alarm_id, body=event_body)
            self.noonlight.last_event = {"event_type": "alarm.device.activated_alarm", "meta": {"value": event_text}}
            from homeassistant.helpers.dispatcher import async_dispatcher_send
            async_dispatcher_send(self.noonlight.hass, "noonlight_alarm_state_changed")
        else:
            _LOGGER.warning("No active alarm to send event to")
