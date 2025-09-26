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
        self._session = session
        self._email = email
        self._password = password
        self._access_token: Optional[str] = None

    async def authenticate(self) -> bool:
        """Authenticate and retrieve the access token."""
        payload = {
            "operationName": "Authenticate",
            "variables": {
                "email": self._email,
                "password": self._password,
                "deviceId": "8858035b-b514-4a7e-b2e1-5e73059425ae",
            },
            "query": (
                "mutation Authenticate($email: EmailAddress!, $password: Password!) "
                "{ me { authenticateWithPassword(email: $email, password: $password) "
                "{ ... on AuthenticationSucceeded { accessToken } } } }"
            ),
        }
        try:
            async with self._session.post(AUTH_URL, json=payload, headers={"Content-Type": "application/json"}) as resp:
                resp.raise_for_status()
                data = await resp.json()
                auth = data.get("data", {}).get("me", {}).get("authenticateWithPassword", {})
                token = auth.get("accessToken")
                if not token:
                    _LOGGER.error("Authentication failed: %s", auth)
                    return False
                self._access_token = token
                return True
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
                children = [
                    {"id": item["id"], "name": item["title"]}
                    for item in data.get("items", [])
                    if item.get("type") == "Famly.Daycare:Child"
                ]
                _LOGGER.info("Found %d children: %s", len(children), [c["name"] for c in children])
                return children
        except Exception:
            _LOGGER.exception("Error fetching or parsing children list from sidebar")
            return None

    async def get_child_status(self, child_id: str) -> Optional[str]:
        """Fetch the latest check-in/check-out status for a child."""
        if not self._access_token and not await self.authenticate():
            return None

        today = datetime.utcnow().strftime("%Y-%m-%d")
        params = {"type": "RANGE", "day": today, "to": today, "childId": child_id}
        headers = {"x-famly-accesstoken": self._access_token}

        try:
            async with self._session.get(CALENDAR_URL, params=params, headers=headers) as response:
                if response.status == 401:
                    _LOGGER.info("Access token expired. Re-authenticating...")
                    if not await self.authenticate():
                        return None
                    headers["x-famly-accesstoken"] = self._access_token
                    async with self._session.get(CALENDAR_URL, params=params, headers=headers) as retry_response:
                        retry_response.raise_for_status()
                        data = await retry_response.json()
                else:
                    response.raise_for_status()
                    data = await response.json()

                if not data:
                    _LOGGER.debug("Calendar empty for child %s -> Outside Childcare", child_id)
                    return STATE_OUTSIDE_CHILDCARE

                def parse_iso(ts: Optional[str]) -> Optional[datetime]:
                    if not ts:
                        return None
                    try:
                        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
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

                # Prefer embed.type (CHECK_IN/CHECK_OUT), then fall back to originator/type/title
                def event_kind(ev: dict) -> Optional[str]:
                    embed = ev.get("embed", {}) if isinstance(ev, dict) else {}
                    et = embed.get("type")
                    if isinstance(et, str):
                        u = et.upper()
                        if u == "CHECK_OUT":
                            return "checkout"
                        if u == "CHECK_IN":
                            return "checkin"
                    origin = ev.get("originator", {}) if isinstance(ev, dict) else {}
                    t = origin.get("type") or origin.get("__typename") or ev.get("type") or ev.get("eventType")
                    k = normalize_type(t)
                    if not k and isinstance(ev.get("title"), str):
                        title = ev["title"].lower()
                        if "sjekket ut" in title or "checked out" in title:
                            return "checkout"
                        if "sjekket inn" in title or "checked in" in title:
                            return "checkin"
                    return k

                # Prefer 'from' timestamp, then occurredAt/timestamp fields
                def event_timestamp(ev: dict) -> Optional[datetime]:
                    origin = ev.get("originator", {}) if isinstance(ev, dict) else {}
                    ts = (
                        ev.get("from")
                        or origin.get("occurredAt")
                        or ev.get("occurredAt")
                        or origin.get("timestamp")
                        or ev.get("timestamp")
                    )
                    return parse_iso(ts)

                # Flatten all events from the response
                candidates: list[dict] = []

                def collect_events(container):
                    if isinstance(container, dict):
                        evs = container.get("events")
                        if isinstance(evs, list):
                            candidates.extend(evs)
                        days = container.get("days")
                        if isinstance(days, list):
                            for day in days:
                                evs2 = day.get("events", [])
                                if isinstance(evs2, list):
                                    candidates.extend(evs2)
                    elif isinstance(container, list):
                        for item in container:
                            collect_events(item)

                collect_events(data)

                latest_time: Optional[datetime] = None
                latest_kind: Optional[str] = None
                for ev in candidates:
                    kind = event_kind(ev)
                    if not kind:
                        continue
                    dt = event_timestamp(ev)
                    if not dt:
                        if latest_time is None:
                            latest_kind = kind
                        continue
                    if latest_time is None or dt > latest_time:
                        latest_time = dt
                        latest_kind = kind

                _LOGGER.debug(
                    "Calendar parse: child=%s candidates=%s latest_kind=%s latest_time=%s (raw_events_sample=%s)",
                    child_id,
                    len(candidates),
                    latest_kind,
                    latest_time.isoformat() if latest_time else None,
                    [
                        {
                            "title": e.get("title"),
                            "embed.type": e.get("embed", {}).get("type") if isinstance(e.get("embed"), dict) else None,
                            "originator.type": e.get("originator", {}).get("type") if isinstance(e.get("originator"), dict) else None,
                            "from": e.get("from"),
                        }
                        for e in candidates[:3]
                    ],
                )

                if latest_kind == "checkin":
                    return STATE_AT_CHILDCARE
                return STATE_OUTSIDE_CHILDCARE

        except Exception:
            _LOGGER.exception("Error fetching calendar data for child %s", child_id)
            return None