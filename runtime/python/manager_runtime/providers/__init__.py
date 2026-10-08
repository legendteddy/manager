"""Provider adapters for the Manager Python reference runtime."""

from .base import (
    ModelAdapter,
    ProviderAdapterError,
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderCapabilities,
    ProviderContextLimitError,
    ProviderInternalError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderUnsupportedCapabilityError,
)
from .openai_adapter import OpenAIResponsesAdapter
from .resilience import ProviderRetryPolicy, ProviderRoute, ResilientModelAdapter
from .synthetic import SyntheticModelAdapter

__all__ = [
    "ModelAdapter",
    "ProviderCapabilities",
    "ProviderAdapterError",
    "ProviderAuthenticationError",
    "ProviderAuthorizationError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderMalformedResponseError",
    "ProviderUnsupportedCapabilityError",
    "ProviderContextLimitError",
    "ProviderInternalError",
    "ProviderRetryPolicy",
    "ProviderRoute",
    "ResilientModelAdapter",
    "SyntheticModelAdapter",
    "OpenAIResponsesAdapter",
]
