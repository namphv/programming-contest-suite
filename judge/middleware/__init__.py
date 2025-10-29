# Import all middleware classes from their respective modules
from .device_security import DeviceSecurityMiddleware

# Import from renamed middleware_legacy.py file
from ..middleware_legacy import (
    ShortCircuitMiddleware,
    DMOJLoginMiddleware, 
    DMOJImpersonationMiddleware,
    ContestMiddleware,
    APIMiddleware,
    MiscConfigMiddleware,
)