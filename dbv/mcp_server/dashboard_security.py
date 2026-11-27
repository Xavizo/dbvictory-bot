"""Process-local dashboard credentials; never written to disk or sent in URLs."""

import secrets

DASHBOARD_ORIGIN = "http://127.0.0.1:4747"
# Kept in this module so dashboard_api's hot reload doesn't rotate the token
# underneath the already running Electron process.
SESSION_TOKEN = secrets.token_urlsafe(32)


def valid_token(value: object) -> bool:
    return isinstance(value, str) and secrets.compare_digest(
        value.encode("utf-8"), SESSION_TOKEN.encode("ascii")
    )


def allowed_origin(value: str | None) -> bool:
    return value == DASHBOARD_ORIGIN
