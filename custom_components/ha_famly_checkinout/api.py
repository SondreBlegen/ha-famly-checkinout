"""API for Famly Childcare."""
import aiohttp
import logging
from datetime import datetime
from typing import Optional, List, Dict

try:
    # Normal import when used inside Home Assistant package
    from .const import (
        AUTH_URL,
        CALENDAR_URL,
        SIDEBAR_URL,
        STATE_OUTSIDE_CHILDCARE,
        STATE_AT_CHILDCARE,
    )
except ImportError:
    # Fallback for standalone debug scripts importing this module directly
    from const import (
        AUTH_URL,
        CALENDAR_URL,
        SIDEBAR_URL,
        STATE_OUTSIDE_CHILDCARE,
        STATE_AT_CHILDCARE,
    )

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
            "variables": {"email": self._email, "password": self._password, "deviceId": "8858035b-b514-4a7e-b2e1-5e73059425ae"},
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
            async with self._session.get(SIDEBAR_URL, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                children = [{"id": item["id"], "name": item["title"]} for item in data.get("items", []) if item.get("type") == "Famly.Daycare:Child"]
                _LOGGER.info(f"Found {len(children)} children: {[c['name'] for c in children]}")
                return children
        except Exception:
            _LOGGER.exception("Error fetching or parsing children list from sidebar")
            return None

    async def get_child_status(self, child_id: str) -> Optional[str]:
        """Fetch the latest check-in/check-out status for a child."""
        if not self._access_token and not await self.authenticate():
            return None

        # Use local date to match the daycare/day view the user sees in Famly
        # Using UTC can shift very early/late events to an adjacent day.
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

                # Robust parsing: tolerate structure/field variations
                if not data:
                    _LOGGER.debug("Calendar empty for child %s -> Outside Childcare", child_id)
                    return STATE_OUTSIDE_CHILDCARE

                def parse_iso(ts: Optional[str]) -> Optional[datetime]:
                    if not ts:
                        return None
                    try:
                        # Handle trailing 'Z' UTC and ensure fromisoformat compatibility
                        ts2 = ts.replace("Z", "+00:00")
                        return datetime.fromisoformat(ts2)
                    except Exception:
                        return None

                def normalize_type(t: Optional[str]) -> Optional[str]:
                    if not t:
                        return None
                    tl = t.lower()
                    if "checkin" in tl or "check_in" in tl:
                        return "checkin"
                    if "checkout" in tl or "check_out" in tl:
                        return "checkout"
                    return None

                # Collect all candidate events for today
                candidates = []

                def collect_events(container):
                    if isinstance(container, dict):
                        # Direct events list
                        evs = container.get("events")
                        if isinstance(evs, list):
                            for ev in evs:
                                candidates.append(ev)
                        # Nested days
                        days = container.get("days")
                        if isinstance(days, list):
                            for day in days:
                                evs2 = day.get("events", [] )
                                for ev in evs2:
                                    candidates.append(ev)
                    elif isinstance(container, list):
                        for item in container:
                            collect_events(item)

                collect_events(data)

                latest_time: Optional[datetime] = None
                latest_kind: Optional[str] = None
                for ev in candidates:
                    origin = ev.get("embed", {}) if isinstance(ev, dict) else {}
                    t = normalize_type(origin.get("type") or origin.get("__typename") or ev.get("type") or ev.get("eventType"))
                    if not t:
                        continue
                    ts = ev.get("from") or ev.get("from")
                    dt = parse_iso(ts)
                    if not dt:
                        # As a fallback, accept events without timestamp but do not override a dated latest
                        if latest_time is None:
                            latest_kind = t
                        continue
                    if latest_time is None or dt > latest_time:
                        latest_time = dt
                        latest_kind = t

                _LOGGER.debug(
                    "Calendar parse: child=%s candidates=%s latest_kind=%s latest_time=%s",
                    child_id,
                    len(candidates),
                    latest_kind,
                    latest_time.isoformat() if latest_time else None,
                )

                if latest_kind == "checkin":
                    return STATE_AT_CHILDCARE
                # If no events or latest was checkout -> outside
                return STATE_OUTSIDE_CHILDCARE

        except Exception:
            # Catch ANY exception during the process and log it. This prevents the integration from crashing.
            _LOGGER.exception(f"Error fetching calendar data for child {child_id}")
            return None  # Return None to indicate failure