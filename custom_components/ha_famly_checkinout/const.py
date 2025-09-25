"""Constants for the Famly Childcare integration."""

DOMAIN = "ha_famly_checkinout"
PLATFORMS = ["sensor", "binary_sensor"]

# API Endpoints
BASE_URL = "https://app.famly.co"
AUTH_URL = f"{BASE_URL}/graphql?Authenticate=null"
SIDEBAR_URL = f"{BASE_URL}/api/v2/sidebar"
CALENDAR_URL = f"{BASE_URL}/api/v2/calendar"

# Configuration
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_CHILDREN = "children"

# Sensor States
STATE_AT_CHILDCARE = "At childcare"
STATE_OUTSIDE_CHILDCARE = "Outside childcare"