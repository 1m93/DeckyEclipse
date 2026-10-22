"""Shared backend modules for the Eclipse Decky plugin.

Everything in this package must stay importable without the ``decky`` module,
because the Steam shortcut launcher (``bin/eclipse_launcher.py``) reuses it
outside of the Decky loader process.
"""

FLATPAK_MOONLIGHT = "com.moonlight_stream.Moonlight"
FLATPAK_SUNSHINE = "dev.lizardbyte.app.Sunshine"

DEFAULT_HTTP_PORT = 47989
DEFAULT_SUNSHINE_WEB_PORT = 47990

REQUEST_FILE = "launch-request.json"
SETTINGS_FILE = "settings.json"
LAUNCHER_LOG = "launcher.log"

# Requests older than this are ignored by the launcher, so launching the
# shortcut from the library later never replays a stale action.
REQUEST_MAX_AGE_S = 120
