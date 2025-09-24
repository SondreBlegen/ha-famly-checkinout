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

from .const import DOMAIN, CONF_CHILDREN, CONF_EMAIL, CONF_PASSWORD, STATE_OUTSIDE_CHILDCARE
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
        name="famly_childcare_sensor",
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
        self._attr_icon = "mdi:human-child"
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