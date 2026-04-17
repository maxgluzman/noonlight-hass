"""Support for Noonlight sensors."""
import logging
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Noonlight sensors from a config entry."""
    noonlight_integration = hass.data[DOMAIN][config_entry.entry_id]
    
    sensors = [
        NoonlightLastEventSensor(noonlight_integration),
        NoonlightNextPollSensor(noonlight_integration),
        NoonlightTriggerTimeSensor(noonlight_integration),
        NoonlightTriggerReasonSensor(noonlight_integration),
    ]
    
    async_add_entities(sensors)
    
    # Connect to dispatchers to force update if needed
    def alarm_state_changed():
        for sensor in sensors:
            sensor.async_schedule_update_ha_state()
            
    async_dispatcher_connect(hass, "noonlight_alarm_state_changed", alarm_state_changed)

class NoonlightSensorBase(SensorEntity):
    """Base class for Noonlight sensors."""
    
    def __init__(self, noonlight_integration):
        """Initialize the sensor."""
        self.noonlight = noonlight_integration
        self._attr_device_info = {
            "identifiers": {(DOMAIN, self.noonlight.config.get('id', 'default'))},
            "name": "Noonlight Alarm",
            "manufacturer": "Noonlight",
            "model": "V2 Dispatch",
        }

class NoonlightLastEventSensor(NoonlightSensorBase):
    """Sensor to show the last event sent to Noonlight."""
    
    def __init__(self, noonlight_integration):
        super().__init__(noonlight_integration)
        self._attr_name = "Last Event Sent"
        self._attr_unique_id = f"last_event_{self.noonlight.config.get('id', 'default')}"
        self._attr_icon = "mdi:message-text"

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        if self.noonlight.last_event:
            return self.noonlight.last_event.get("event_type")
        return "None"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        if self.noonlight.last_event:
            return self.noonlight.last_event.get("meta", {})
        return {}

class NoonlightNextPollSensor(NoonlightSensorBase):
    """Sensor to show the next poll time."""
    
    def __init__(self, noonlight_integration):
        super().__init__(noonlight_integration)
        self._attr_name = "Next Poll Time"
        self._attr_unique_id = f"next_poll_{self.noonlight.config.get('id', 'default')}"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:timer"

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.noonlight.next_poll_time

class NoonlightTriggerTimeSensor(NoonlightSensorBase):
    """Sensor to show when the alarm was triggered."""
    
    def __init__(self, noonlight_integration):
        super().__init__(noonlight_integration)
        self._attr_name = "Triggered Time"
        self._attr_unique_id = f"trigger_time_{self.noonlight.config.get('id', 'default')}"
        self._attr_device_class = SensorDeviceClass.TIMESTAMP
        self._attr_icon = "mdi:bell-ring"

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.noonlight.trigger_time

class NoonlightTriggerReasonSensor(NoonlightSensorBase):
    """Sensor to show the reason for the trigger."""
    
    def __init__(self, noonlight_integration):
        super().__init__(noonlight_integration)
        self._attr_name = "Trigger Reason"
        self._attr_unique_id = f"trigger_reason_{self.noonlight.config.get('id', 'default')}"
        self._attr_icon = "mdi:comment-question"

    @property
    def native_value(self):
        """Return the native value of the sensor."""
        return self.noonlight.trigger_reason
