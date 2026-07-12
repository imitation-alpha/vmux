"""Public client/server compatibility policy advertised by the REST API."""

PROTOCOL_VERSION = 1
MINIMUM_IOS_VERSION = "1.0.0"


def compatibility_info() -> dict:
    """Return a fresh wire payload so callers cannot mutate the policy."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "minimum_ios_version": MINIMUM_IOS_VERSION,
    }
