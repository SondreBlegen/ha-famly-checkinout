"""Binary sensor for Famly Childcare presence (at childcare = on)."""
import asyncio
from datetime import timedelta
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_CHILDREN,
    CONF_EMAIL,
    CONF_PASSWORD,
    STATE_AT_CHILDCARE,
)
from .api import FamlyApi

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=10)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary_sensor platform."""
    session = async_get_clientsession(hass)
    api = FamlyApi(
        session,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
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
        name="ha_famly_checkinout_presence",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    await coordinator.async_config_entry_first_refresh()

    entities = [
        ChildcarePresenceBinarySensor(coordinator, entry.entry_id, child_id, child_name)
        for child_id, child_name in selected_children.items()
    ]
    async_add_entities(entities, True)


class ChildcarePresenceBinarySensor(BinarySensorEntity):
    """Binary sensor that is on when the child is at childcare."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, coordinator: DataUpdateCoordinator, entry_id: str, child_id: str, child_name: str) -> None:
        self.coordinator = coordinator
        self._child_id = child_id
        self._attr_name = f"Childcare Presence {child_name}"
        self._attr_unique_id = f"{entry_id}_{child_id}_presence"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": f"Famly ({coordinator.hass.config_entries.async_get_entry(entry_id).data.get(CONF_EMAIL)})",
            "manufacturer": "Famly",
        }

    @property
    def is_on(self) -> bool:
        """Return true if the child is currently at childcare."""
        state = self.coordinator.data.get(self._child_id)
        return state == STATE_AT_CHILDCARE

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self._child_id in self.coordinator.data
