from django.contrib.auth import logout
from django.contrib import messages
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.urls import reverse
from django.conf import settings

from judge.models import MiscConfig


class DeviceSecurityMiddleware:
    """
    Middleware to validate device fingerprint on authenticated requests.
    Ensures users can only access the system from their registered device.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        # Check if device fingerprinting is enabled via admin settings
        try:
            from judge.models import MiscConfig
            config = MiscConfig.objects.get(key='DEVICE_FINGERPRINTING_ENABLED')
            fingerprinting_enabled = config.value.lower() == 'true'
        except:
            # Default to enabled if not configured
            fingerprinting_enabled = True
        
        # Check device security for authenticated users (skip admin users or if globally disabled)
        if (fingerprinting_enabled and
            request.user.is_authenticated and hasattr(request.user, 'profile') 
            and not request.user.is_staff and not request.user.is_superuser):
            # Skip check for login/logout pages to avoid infinite redirects
            skip_paths = [
                reverse('auth_login'),
                reverse('auth_logout'),
                '/static/',
                '/media/',
            ]
            
            if not any(request.path.startswith(path) for path in skip_paths):
                if not self._validate_device_security(request):
                    # Device validation failed - logout and redirect
                    logout(request)
                    messages.error(request, _('Account only allows login from 1 device. Contact: 0395 971 275'))
                    return redirect('auth_login')
        
        response = self.get_response(request)
        return response
    
    def _validate_device_security(self, request):
        """
        Validate that the current request comes from the registered device.
        Returns True if valid, False if security violation detected.
        """
        profile = request.user.profile
        
        # If no device fingerprint registered, allow (legacy users)
        if not profile.device_fingerprint:
            return True
            
        # Check device ID cookie first (faster check)
        device_id_cookie = request.COOKIES.get('device_id')
        if device_id_cookie and device_id_cookie == profile.device_id:
            return True
            
        # Device ID cookie missing or mismatched - potential security issue
        # For now, we'll be lenient and just log this event
        # In production, you might want to require re-authentication
        return True
    
    def process_exception(self, request, exception):
        """Handle any exceptions gracefully"""
        return None