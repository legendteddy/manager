"""Network-facing service boundaries for Manager.

``gateway`` is the production entrypoint. ``server`` remains the smaller
reference/control-plane HTTP path used by existing tests and examples.
"""

from .api import ApiError, DurableRuntimeBackend, ServiceBackend
from .gateway import (
    GatewayContext,
    GatewaySettings,
    create_gateway_server,
    load_gateway_settings,
    run_service,
)
from .http_policy import install_http_policy
from .server import (
    ManagerServiceContext,
    ManagerServiceSettings,
    create_service_server,
    load_service_settings,
    run_service as run_reference_service,
)

# Importing any manager_runtime.service submodule first initializes this package.
# Install the HTTP/1.1 framing policy once before callers can construct a gateway.
install_http_policy()

__all__ = [
    "ApiError",
    "ServiceBackend",
    "DurableRuntimeBackend",
    "GatewayContext",
    "GatewaySettings",
    "create_gateway_server",
    "load_gateway_settings",
    "run_service",
    "ManagerServiceContext",
    "ManagerServiceSettings",
    "create_service_server",
    "load_service_settings",
    "run_reference_service",
]
