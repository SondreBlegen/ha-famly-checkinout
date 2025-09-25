"""Sensor platform for Famly Childcare."""
import asyncio
from datetime import timedelta
import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_CHILDREN, CONF_EMAIL, CONF_PASSWORD, STATE_OUTSIDE_CHILDCARE, STATE_AT_CHILDCARE
from .api import FamlyApi

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=10)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    session = async_get_clientsession(hass)
    api = FamlyApi(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD]
    )
    
    selected_children = entry.data[CONF_CHILDREN]

    async def async_update_data():
        """Fetch data from API for all configured children."""
        tasks = [api.get_child_status(child_id) for child_id in selected_children]
        results = await asyncio.gather(*tasks)
        
        if any(status is None for status in results):
             _LOGGER.warning("Failed to retrieve status for one or more children.")
        
        return {child_id: status for child_id, status in zip(selected_children.keys(), results)}

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="ha_famly_checkinout_sensor",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )
    
    await coordinator.async_config_entry_first_refresh()

    sensors = [
        ChildcareStatusSensor(coordinator, entry.entry_id, child_id, child_name)
        for child_id, child_name in selected_children.items()
    ]
    async_add_entities(sensors, True)


class ChildcareStatusSensor(SensorEntity):
    """Representation of a Childcare Status Sensor."""

    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, child_id: str, child_name: str):
        """Initialize the sensor."""
        self.coordinator = coordinator
        self._child_id = child_id
        
        self._attr_name = f"Childcare Status {child_name}"
        self._attr_unique_id = f"{entry_id}_{child_id}"
        # Icon changes based on state for better visual cue
        self._attr_icon = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": f"Famly ({coordinator.hass.config_entries.async_get_entry(entry_id).data.get(CONF_EMAIL)})",
            "manufacturer": "Famly",
        }

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data.get(self._child_id) or STATE_OUTSIDE_CHILDCARE

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self._child_id in self.coordinator.data

    @property
    def icon(self) -> str:
        """Return an icon representing the child's current status."""
        current = self.state
        if current == STATE_AT_CHILDCARE:
            # Filled icon when present
            return "mdi:school"
        # Outline when absent
        return "mdi:school-outline"

    @property
    def extra_state_attributes(self) -> dict:
        """Provide attributes that UI cards can use for styling/conditions."""
        current = self.state
        at_childcare = current == STATE_AT_CHILDCARE
        # icon_color can be leveraged by some cards/themes; core may ignore it.
        return {
            "childcare_present": at_childcare,
            "icon_color": "green" if at_childcare else "grey",
        }