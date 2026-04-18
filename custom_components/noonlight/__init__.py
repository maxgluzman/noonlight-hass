"""Noonlight integration for Home Assistant."""

import logging
import json
import time
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import homeassistant.util.dt as dt_util
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ID, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import DOMAIN as HOMEASSISTANT_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_time_interval,
)
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.typing import ConfigType

import noonlight as nl

from .const import (
    CONF_ADDRESS_LINE1,
    CONF_ADDRESS_LINE2,
    CONF_API_ENDPOINT,
    CONF_CITY,
    CONF_SECRET,
    CONF_STATE,
    CONF_TOKEN_ENDPOINT,
    CONF_ZIP,
    CONF_TEST_TOKEN,
    CONF_TEST_API_ENDPOINT,
    CONF_ALARM_NAME,
    CONF_ALARM_PHONE,
    CONF_ALARM_PIN,
    MODE_PRODUCTION,
    MODE_SANDBOX,
    CONST_ALARM_STATUS_ACTIVE,
    CONST_ALARM_STATUS_CANCELED,
    CONST_NOONLIGHT_HA_SERVICE_CREATE_ALARM,
    CONST_NOONLIGHT_SERVICE_TYPES,
    DOMAIN,
    EVENT_NOONLIGHT_ALARM_CANCELED,
    EVENT_NOONLIGHT_ALARM_CREATED,
    EVENT_NOONLIGHT_TOKEN_REFRESHED,
    NOTIFICATION_ALARM_CREATE_FAILURE,
    NOTIFICATION_TOKEN_UPDATE_FAILURE,
    NOTIFICATION_TOKEN_UPDATE_SUCCESS,
    PLATFORMS,
)

_LOGGER = logging.getLogger(__name__)
TOKEN_CHECK_INTERVAL = timedelta(minutes=15)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_ID): cv.string,
                vol.Required(CONF_SECRET): cv.string,
                vol.Required(CONF_API_ENDPOINT): cv.string,
                vol.Required(CONF_TOKEN_ENDPOINT): cv.string,
                vol.Optional(CONF_ADDRESS_LINE1): cv.string,
                vol.Optional(CONF_ADDRESS_LINE2): cv.string,
                vol.Optional(CONF_CITY): cv.string,
                vol.Optional(CONF_STATE): cv.string,
                vol.Optional(CONF_ZIP): cv.string,
                vol.Inclusive(
                    CONF_LATITUDE, "coordinates", "Include both latitude and longitude"
                ): cv.latitude,
                vol.Inclusive(
                    CONF_LONGITUDE, "coordinates", "Include both latitude and longitude"
                ): cv.longitude,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up from YAML."""
    if DOMAIN not in config:
        return True

    _LOGGER.debug(f"[async_setup] config: {config[DOMAIN]}")
    async_create_issue(
        hass,
        HOMEASSISTANT_DOMAIN,
        f"deprecated_yaml_{DOMAIN}",
        breaks_in_ha_version="2025.1",
        is_fixable=False,
        is_persistent=False,
        issue_domain=DOMAIN,
        severity=IssueSeverity.WARNING,
        translation_key="deprecated_yaml",
        translation_placeholders={
            "domain": DOMAIN,
            "integration_title": "Noonlight",
        },
    )

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IMPORT},
            data=config[DOMAIN],
        )
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up from a config entry."""

    _LOGGER.debug(f"[init async_setup_entry] entry: {entry.data}")
    noonlight_integration = NoonlightIntegration(hass, entry.data)
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = noonlight_integration

    async def handle_create_alarm_service(call):
        """Create a noonlight alarm from a service"""
        service = call.data.get("service", None)
        name = call.data.get("name", None)
        phone = call.data.get("phone", None)
        pin = call.data.get("pin", None)
        workflow_id = call.data.get("workflow_id", None)
        await noonlight_integration.create_alarm(
            alarm_types=[service], name=name, phone=phone, pin=pin, workflow_id=workflow_id
        )

    hass.services.async_register(
        DOMAIN, CONST_NOONLIGHT_HA_SERVICE_CREATE_ALARM, handle_create_alarm_service
    )

    async def handle_send_event_service(call):
        """Send an event to an active alarm"""
        event_type = call.data.get("event_type")
        meta = call.data.get("meta", {})
        if noonlight_integration._alarm is not None:
            alarm_id = noonlight_integration._alarm.id
            event_body = [{
                "event_type": event_type,
                "event_time": dt_util.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "meta": meta
            }]
            noonlight_integration.show_api_diagnostic("Send Event", noonlight_integration.active_client, f"{noonlight_integration.active_client.alarms_url}/{alarm_id}/events", event_body)
            await noonlight_integration.active_client.create_event(id=alarm_id, body=event_body)
            noonlight_integration.last_event = {"event_type": event_type, "meta": meta}
            from homeassistant.helpers.dispatcher import async_dispatcher_send
            async_dispatcher_send(hass, "noonlight_alarm_state_changed")
        else:
            _LOGGER.warning("No active alarm to send event to")

    hass.services.async_register(
        DOMAIN, "send_event", handle_send_event_service
    )

    async def handle_add_person_service(call):
        """Add people to an active alarm"""
        people = call.data.get("people", [])
        if noonlight_integration._alarm is not None:
            alarm_id = noonlight_integration._alarm.id
            await noonlight_integration.active_client.create_people(id=alarm_id, body=people)
        else:
            _LOGGER.warning("No active alarm to add people to")

    hass.services.async_register(
        DOMAIN, "add_person", handle_add_person_service
    )

    async def handle_cancel_alarm_service(call):
        """Cancel an active alarm"""
        pin = call.data.get("pin")
        await noonlight_integration.cancel_alarm(pin=pin)

    hass.services.async_register(
        DOMAIN, "cancel_alarm", handle_cancel_alarm_service
    )

    async def handle_create_verification_service(call):
        """Create a verification task"""
        body = {
            "prompt": call.data.get("prompt"),
            "attachments": call.data.get("attachments", [])
        }
        for key in ["person_id", "location_id", "device_id"]:
            if key in call.data:
                body[key] = call.data[key]
        
        await noonlight_integration.active_client.create_verification(body=body)

    hass.services.async_register(
        DOMAIN, "create_verification", handle_create_verification_service
    )

    async def check_api_token(now):
        """Check if the current API token has expired and renew if so."""
        next_check_interval = TOKEN_CHECK_INTERVAL

        result = await noonlight_integration.check_api_token()

        if not result:
            _LOGGER.error("API token failed renewal, retrying in 3 min")
            check_api_token.fail_count += 1
            persistent_notification.create(
                hass,
                "Noonlight API token failed to renew {} time{}!\n"
                "Home Assistant will automatically attempt to renew the "
                "API token in 3 minutes.".format(
                    check_api_token.fail_count,
                    "s" if check_api_token.fail_count > 1 else "",
                ),
                "Noonlight Token Renewal Failure",
                NOTIFICATION_TOKEN_UPDATE_FAILURE,
            )
            next_check_interval = timedelta(minutes=3)
        else:
            if check_api_token.fail_count > 0:
                persistent_notification.create(
                    hass,
                    "Noonlight API token has now been " "renewed successfully.",
                    "Noonlight Token Renewal Success",
                    NOTIFICATION_TOKEN_UPDATE_SUCCESS,
                )
            check_api_token.fail_count = 0

        async_track_point_in_utc_time(
            hass, check_api_token, dt_util.utcnow() + next_check_interval
        )

    check_api_token.fail_count = 0

    async_track_point_in_utc_time(hass, check_api_token, dt_util.utcnow())

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info(f"Unloading: {entry.data}")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN)

    return unload_ok


class NoonlightException(HomeAssistantError):
    """General exception for Noonlight Integration."""

    pass


class NoonlightIntegration:
    """Integration for interacting with Noonlight from Home Assistant."""

    def __init__(self, hass, conf):
        """Initialize NoonlightIntegration."""
        self.hass = hass
        self.config = conf
        self._access_token_response = {}
        self._alarm = None
        self._time_to_renew = timedelta(hours=2)
        self._websession = async_get_clientsession(self.hass)
        self.client = nl.NoonlightClient(
            token=self.access_token, session=self._websession
        )
        self.client.set_base_url(self.config[CONF_API_ENDPOINT])

        self.test_token = self.config.get(CONF_TEST_TOKEN, "")
        self.test_api_endpoint = self.config.get(CONF_TEST_API_ENDPOINT, "https://api-sandbox.noonlight.com/dispatch/v1")
        
        self.test_client = None
        if self.test_token:
            self.test_client = nl.NoonlightClient(
                token=self.test_token, session=self._websession
            )
            self.test_client.set_base_url(self.test_api_endpoint)
            
        self.active_mode = MODE_PRODUCTION
        self.alarm_name = self.config.get(CONF_ALARM_NAME, "")
        self.alarm_phone = self.config.get(CONF_ALARM_PHONE, "")
        self.alarm_pin = self.config.get(CONF_ALARM_PIN, "")

        # Add address portions, if exist
        self.addline1 = self.config.get(CONF_ADDRESS_LINE1, "")
        self.addline2 = self.config.get(CONF_ADDRESS_LINE2, "")
        self.addcity = self.config.get(CONF_CITY, "")
        self.addstate = self.config.get(CONF_STATE, "")
        self.addzip = self.config.get(CONF_ZIP, "")
        
        self.last_event = None
        self.trigger_time = None
        self.trigger_reason = None
        self.next_poll_time = None
        self._status_poll_interval = None

    @property
    def active_client(self):
        """Return the client for the active mode."""
        if self.active_mode == MODE_SANDBOX and self.test_client:
            return self.test_client
        return self.client

    def show_api_diagnostic(self, title: str, client, endpoint: str, body: dict):
        """Show a persistent notification with API call details."""
        token = client.token if hasattr(client, 'token') else "N/A"
        
        # Mask the token for safety (show only last 4 chars)
        masked_token = token
        if token and len(token) > 4:
             masked_token = "*" * (len(token) - 4) + token[-4:]
             
        message = f"**Endpoint**: {endpoint}\n\n"
        message += f"**Token**: `{masked_token}`\n\n"
        message += f"**Payload**:\n```json\n{json.dumps(body, indent=2)}\n```"
        
        persistent_notification.create(
            self.hass,
            message,
            title=f"Noonlight Diagnostic: {title}",
            notification_id=f"noonlight_diagnostic_{int(time.time())}"
        )

    @property
    def latitude(self):
        """Return latitude from the Home Assistant configuration."""
        return self.config.get(CONF_LATITUDE, self.hass.config.latitude)

    @property
    def longitude(self):
        """Return longitude from the Home Assistant configuration."""
        return self.config.get(CONF_LONGITUDE, self.hass.config.longitude)

    @property
    def access_token(self):
        """Return the access token from the Noonlight Configuration."""
        return self._access_token_response.get("token")

    @property
    def access_token_expiry(self):
        """Return the timestamp when the access token expires."""
        return self._access_token_response.get("expires", dt_util.utc_from_timestamp(0))

    @property
    def access_token_expires_in(self):
        """Will return the timedelta when the token expires."""
        return self.access_token_expiry - dt_util.utcnow()

    @property
    def should_token_be_renewed(self):
        """Will return true if the token needs to be renewed."""
        return (
            self.access_token is None
            or self.access_token_expires_in <= self._time_to_renew
        )

    async def check_api_token(self, force_renew=False):
        """Check if Noonlight API token needs renewal and renew if so."""
        _LOGGER.debug(
            "Checking if token needs renewal, expires: {0:.1f}h".format(
                self.access_token_expires_in.total_seconds() / 3600.0
            )
        )
        if self.should_token_be_renewed or force_renew:
            try:
                _LOGGER.debug("Renewing Noonlight access token")
                path = self.config.get(CONF_TOKEN_ENDPOINT)
                data = {
                    "id": self.config.get(CONF_ID),
                    "secret": self.config.get(CONF_SECRET),
                }
                headers = {"Content-Type": "application/json"}
                token_response = {}
                async with self._websession.post(
                    path, json=data, headers=headers
                ) as resp:
                    token_response = await resp.json()
                if "token" in token_response and "expires" in token_response:
                    self._set_token_response(token_response)
                    _LOGGER.debug("Token set: {}".format(self.access_token))
                    _LOGGER.debug(
                        "Token renewed, expires at {0} ({1:.1f}h)".format(
                            self.access_token_expiry,
                            self.access_token_expires_in.total_seconds() / 3600.0,
                        )
                    )
                    async_dispatcher_send(self.hass, EVENT_NOONLIGHT_TOKEN_REFRESHED)
                    return True
                raise NoonlightException(
                    "unexpected token_response: {}".format(token_response)
                )
            except NoonlightException:
                _LOGGER.exception("Failed to renew Noonlight token")
                return False
        return True

    def _set_token_response(self, token_response):
        expires = dt_util.parse_datetime(token_response["expires"])
        if expires is not None:
            token_response["expires"] = expires
        else:
            token_response["expires"] = dt_util.utc_from_timestamp(0)
        self.client.set_token(token=token_response.get("token"))
        self._access_token_response = token_response

    async def update_alarm_status(self):
        """Update the status of the current alarm."""
        if self._alarm is not None:
            return await self._alarm.get_status()

    async def create_alarm(self, alarm_types=[nl.NOONLIGHT_SERVICES_POLICE], name=None, phone=None, pin=None, workflow_id=None):
        """Create a new alarm"""
        services = {}
        for alarm_type in alarm_types or ():
            if alarm_type in CONST_NOONLIGHT_SERVICE_TYPES:
                services[alarm_type] = True
        if self._alarm is None:
            try:
                alarm_body = {}
                actual_name = name or self.alarm_name
                actual_phone = phone or self.alarm_phone
                if actual_name:
                    alarm_body["name"] = actual_name
                if actual_phone:
                    alarm_body["phone"] = actual_phone
                if pin:
                    alarm_body["pin"] = pin
                if workflow_id:
                    alarm_body["workflow_id"] = workflow_id

                if len(self.addline1) > 0:
                    alarm_body["location"] = {
                        "address": {
                            "line1": self.addline1,
                            "city": self.addcity,
                            "state": self.addstate,
                            "zip": self.addzip,
                        }
                    }
                    if len(self.addline2) > 0:
                        alarm_body["location"]["address"]["line2"] = self.addline2
                else:
                    alarm_body["location"] = {
                        "coordinates": {
                            "lat": self.latitude,
                            "lng": self.longitude,
                            "accuracy": 5,
                        }
                    }
                if len(services) > 0:
                    alarm_body["services"] = services
                self.show_api_diagnostic("Create Alarm", self.active_client, self.active_client.alarms_url, alarm_body)
                self._alarm = await self.active_client.create_alarm(body=alarm_body)
            except nl.NoonlightClient.ClientError as client_error:
                persistent_notification.create(
                    self.hass,
                    "Failed to send an alarm to Noonlight!\n\n"
                    "({}: {})".format(type(client_error).__name__, str(client_error)),
                    "Noonlight Alarm Failure",
                    NOTIFICATION_ALARM_CREATE_FAILURE,
                )
            if self._alarm and self._alarm.status == CONST_ALARM_STATUS_ACTIVE:
                self.trigger_time = dt_util.utcnow()
                self.trigger_reason = f"Manually triggered via switch ({alarm_types})"
                async_dispatcher_send(self.hass, EVENT_NOONLIGHT_ALARM_CREATED)
                async_dispatcher_send(self.hass, "noonlight_alarm_state_changed")
                _LOGGER.debug(
                    "noonlight alarm has been initiated. " "id: %s status: %s",
                    self._alarm.id,
                    self._alarm.status,
                )
                async def check_alarm_status_interval(now):
                    _LOGGER.debug("checking alarm status...")
                    self.next_poll_time = dt_util.utcnow() + timedelta(seconds=15)
                    async_dispatcher_send(self.hass, "noonlight_alarm_state_changed")
                    
                    if await self.update_alarm_status() == CONST_ALARM_STATUS_CANCELED:
                        _LOGGER.debug("alarm %s has been canceled!", self._alarm.id)
                        self.next_poll_time = None
                        if self._status_poll_interval is not None:
                            self._status_poll_interval()
                            self._status_poll_interval = None
                        if self._alarm is not None:
                            if self._alarm.status == CONST_ALARM_STATUS_CANCELED:
                                self._alarm = None
                        async_dispatcher_send(self.hass, EVENT_NOONLIGHT_ALARM_CANCELED)
                        async_dispatcher_send(self.hass, "noonlight_alarm_state_changed")

                self._status_poll_interval = async_track_time_interval(
                    self.hass, check_alarm_status_interval, timedelta(seconds=15)
                )
                self.next_poll_time = dt_util.utcnow() + timedelta(seconds=15)
                async_dispatcher_send(self.hass, "noonlight_alarm_state_changed")

    async def cancel_alarm(self, pin=None):
        """Cancel the active alarm via direct API call."""
        from homeassistant.helpers.dispatcher import async_dispatcher_send
        if self._alarm is not None:
            alarm_id = self._alarm.id
            
            if self.active_mode == "sandbox":
                token = self.test_token
            else:
                token = self._access_token_response.get("token")
                
            url = f"{self.active_client.alarms_url}/{alarm_id}/status"
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            payload = {
                "status": "CANCELED"
            }
            actual_pin = pin if pin is not None else self.alarm_pin
            if actual_pin:
                payload["pin"] = actual_pin
            
            _LOGGER.debug("Cancelling alarm via direct API call to %s", url)
            
            self.show_api_diagnostic("Cancel Alarm", self.active_client, url, payload)
            
            async with self._websession.post(url, headers=headers, json=payload) as resp:
                if resp.status in (200, 204, 201):
                    _LOGGER.info("Successfully cancelled alarm %s", alarm_id)
                    self._alarm = None
                    if self._status_poll_interval is not None:
                        self._status_poll_interval()
                        self._status_poll_interval = None
                    self.next_poll_time = None
                    async_dispatcher_send(self.hass, "noonlight_alarm_state_changed")
                    return True
                else:
                    error_text = await resp.text()
                    try:
                        error_json = json.loads(error_text)
                        if error_json.get("key") == "alarm_canceled" or "already been canceled" in error_json.get("details", "").lower():
                            _LOGGER.info("Alarm %s was already cancelled. Syncing state to idle.", alarm_id)
                            self._alarm = None
                            if self._status_poll_interval is not None:
                                self._status_poll_interval()
                                self._status_poll_interval = None
                            self.next_poll_time = None
                            async_dispatcher_send(self.hass, "noonlight_alarm_state_changed")
                            return True
                    except json.JSONDecodeError:
                        pass
                    _LOGGER.error("Failed to cancel alarm %s: %s", alarm_id, error_text)
                    return False
        else:
            _LOGGER.warning("No active alarm to cancel")
            return False
