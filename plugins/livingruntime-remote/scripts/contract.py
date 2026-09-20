from __future__ import annotations

CORE_TOOLS = (
    "connection_status",
    "read_file",
    "git",
    "logs",
)

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_HTTP_PORT = 8766
PLUGIN_NAME = "LivingRuntime Remote"
PLUGIN_VERSION = "0.2.0"
SECRET_KEYS = (
    "token",
    "password",
    "secret",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
    "identity_file",
)
