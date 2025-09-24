"""Config flow for Famly Childcare."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, CONF_CHILDREN
from .api import FamlyApi

class FamlyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Famly Childcare."""

    VERSION = 1
    
    def __init__(self):
        """Initialize the config flow."""
        self.data = {}
        self.api: FamlyApi = None
        self.children: dict = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step (authentication)."""
        errors = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            self.api = FamlyApi(
                session,
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD]
            )

            if await self.api.authenticate():
                self.data = user_input
                return await self.async_step_children()
            else:
                errors["base"] = "auth"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_children(self, user_input=None):
        """Handle the step to select children."""
        errors = {}
        if user_input is None:
            children_list = await self.api.get_children()
            if not children_list:
                return self.async_abort(reason="no_children")
            
            self.children = {child["id"]: child["name"] for child in children_list}
            
            return self.async_show_form(
                step_id="children",
                data_schema=vol.Schema({
                    vol.Required(CONF_CHILDREN): cv.multi_select(self.children)
                }),
                errors=errors,
            )

        selected_child_ids = user_input[CONF_CHILDREN]
        selected_children = {
            child_id: self.children[child_id] for child_id in selected_child_ids
        }
        
        self.data[CONF_CHILDREN] = selected_children

        return self.async_create_entry(
            title=self.data[CONF_EMAIL],
            data=self.data
        )