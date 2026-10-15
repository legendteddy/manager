"""Network-facing service boundaries for Manager.

``gateway`` is the production entrypoint. ``server`` remains the smaller
reference/control-plane HTTP path used by existing tests and examples.
"""

from . import gateway as _gateway
from .api import ApiError, DurableRuntimeBackend, ServiceBackend
from .http_policy import install_http_policy
from .server import (
    ManagerServiceContext,
    ManagerServiceSettings,
    create_service_server,
    load_service_settings,
    run_service as run_reference_service,
)

# Importing any manager_runtime.service submodule first initializes this package.
# Install the HTTP/config policy before exporting construction helpers so direct
# package callers cannot retain the pre-hardening factory object.
install_http_policy()

GatewayContext = _gateway.GatewayContext
GatewaySettings = _gateway.GatewaySettings
create_gateway_server = _gateway.create_gateway_server
load_gateway_settings = _gateway.load_gateway_settings
run_service = _gateway.run_service

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
