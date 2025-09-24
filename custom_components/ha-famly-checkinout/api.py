"""API for Famly Childcare."""
import aiohttp
import logging
from datetime import datetime
from typing import Optional, List, Dict

from .const import AUTH_URL, CALENDAR_URL, SIDEBAR_URL

_LOGGER = logging.getLogger(__name__)

class FamlyApi:
    """A class for interacting with the Famly API."""

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str):
        """Initialize the API client."""
        self._session = session
        self._email = email
        self._password = password
        self._access_token: Optional[str] = None

    async def authenticate(self) -> bool:
        """Authenticate and retrieve the access token."""
        auth_payload = {
            "operationName": "Authenticate",
            "variables": {"email": self._email, "password": self._password},
            "query": "mutation Authenticate($email: EmailAddress!, $password: Password!) { me { authenticateWithPassword(email: $email, password: $password) { ... on AuthenticationSucceeded { accessToken } } } }"
        }
        headers = {"Content-Type": "application/json"}
        try:
            async with self._session.post(AUTH_URL, json=auth_payload, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                auth_result = data.get("data", {}).get("me", {}).get("authenticateWithPassword", {})
                if "accessToken" in auth_result:
                    self._access_token = auth_result["accessToken"]
                    _LOGGER.info("Successfully authenticated.")
                    return True
                else:
                    _LOGGER.error("Authentication failed: %s", auth_result)
                    return False
        except aiohttp.ClientError as err:
            _LOGGER.error("Error during authentication: %s", err)
            return False

    async def get_children(self) -> Optional[List[Dict[str, str]]]:
        """Fetch the list of children from the sidebar endpoint."""
        if not self._access_token:
            _LOGGER.error("Cannot get children without an access token.")
            return None
        
        headers = {"x-famly-accesstoken": self._access_token}
        try:
            _LOGGER.debug("Fetching sidebar data to find children.")
            async with self._session.get(SIDEBAR_URL, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                
                children = []
                for item in data.get("items", []):
                    if item.get("type") == "Famly.Daycare:Child":
                        children.append({"id": item["id"], "name": item["title"]})
                
                _LOGGER.info(f"Found {len(children)} children: {[c['name'] for c in children]}")
                return children
        except (aiohttp.ClientError, KeyError, TypeError) as err:
            _LOGGER.error("Error fetching or parsing children list from sidebar: %s", err)
            return None

    async def get_child_status(self, child_id: str) -> Optional[str]:
        """Fetch the latest check-in/check-out status for a child."""
        if not self._access_token:
            if not await self.authenticate():
                return None

        today = datetime.utcnow().strftime('%Y-%m-%d')
        params = {"type": "RANGE", "day": today, "to": today, "childId": child_id}
        headers = {"x-famly-accesstoken": self._access_token}

        try:
            async with self._session.get(CALENDAR_URL, params=params, headers=headers) as response:
                if response.status == 401:
                    _LOGGER.info("Access token expired. Re-authenticating...")
                    if await self.authenticate():
                        headers["x-famly-accesstoken"] = self._access_token
                        async with self._session.get(CALENDAR_URL, params=params, headers=headers) as retry_response:
                            retry_response.raise_for_status()
                            data = await retry_response.json()
                    else:
                        return None
                else:
                    response.raise_for_status()
                    data = await response.json()
                
                latest_event_type = None
                latest_event_time = None

                if data and "days" in data[0] and data[0]["days"]:
                    for event in data[0]["days"][0].get("events", []):
                        originator = event.get("originator", {})
                        event_type = originator.get("type")
                        if event_type in ["Famly.Daycare:ChildCheckin", "Famly.Daycare:ChildCheckout"]:
                            event_time_str = originator.get("occurredAt")
                            if event_time_str:
                                event_time = datetime.fromisoformat(event_time_str)
                                if latest_event_time is None or event_time > latest_event_time:
                                    latest_event_time = event_time
                                    latest_event_type = event_type
                
                from .const import STATE_AT_CHILDCARE, STATE_OUTSIDE_CHILDCARE
                return STATE_AT_CHILDCARE if latest_event_type == "Famly.Daycare:ChildCheckin" else STATE_OUTSIDE_CHILDCARE

        except aiohttp.ClientError as err:
            _LOGGER.error("Error fetching calendar data for child %s: %s", child_id, err)
        return None