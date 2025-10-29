"""
Device Security Utilities
Helper functions for device fingerprinting configuration and validation.
"""

from django.conf import settings
from django.contrib.sites.shortcuts import get_current_site


def get_device_fingerprinting_enabled(request):
    """
    Get device fingerprinting enabled status with robust domain handling.
    
    This function handles multiple scenarios:
    1. Domain-specific configs (e.g., oj.tica.edu.vn:DEVICE_FINGERPRINTING_ENABLED)
    2. Global configs (DEVICE_FINGERPRINTING_ENABLED)
    3. Settings fallback
    4. Graceful failure handling
    
    Args:
        request: Django HttpRequest object
        
    Returns:
        bool: True if device fingerprinting is enabled, False otherwise
    """
    from judge.models import MiscConfig
    
    try:
        # Get current site domain
        try:
            current_site = get_current_site(request)
            domain = current_site.domain
        except:
            domain = getattr(settings, 'ALLOWED_HOSTS', ['localhost'])[0] if getattr(settings, 'ALLOWED_HOSTS', None) else 'localhost'
        
        # Try multiple config key variations in order of preference
        config_keys = [
            f'{domain}:DEVICE_FINGERPRINTING_ENABLED',  # Domain-specific
            'DEVICE_FINGERPRINTING_ENABLED',           # Global
        ]
        
        for key in config_keys:
            try:
                config = MiscConfig.objects.get(key=key)
                return config.value.lower() in ['true', '1', 'yes', 'on']
            except MiscConfig.DoesNotExist:
                continue
        
        # If no MiscConfig found, try request.misc_config if available
        if hasattr(request, 'misc_config'):
            try:
                value = request.misc_config.get('DEVICE_FINGERPRINTING_ENABLED', None)
                if value is not None:
                    return value.lower() in ['true', '1', 'yes', 'on']
            except:
                pass
        
        # Fallback to Django settings
        return getattr(settings, 'DEVICE_FINGERPRINTING_ENABLED', False)
        
    except Exception as e:
        # Log error but don't break the application
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f'Device fingerprinting config error: {e}')
        
        # Safe default: disabled
        return False


def ensure_device_fingerprinting_config():
    """
    Ensure device fingerprinting configuration exists in database.
    Creates default config if none exists.
    """
    from judge.models import MiscConfig
    
    try:
        # Ensure global config exists
        global_config, created = MiscConfig.objects.get_or_create(
            key='DEVICE_FINGERPRINTING_ENABLED',
            defaults={'value': 'false'}  # Default to disabled for security
        )
        
        if created:
            print(f'Created global device fingerprinting config: {global_config.key} = {global_config.value}')
        
        return global_config
        
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Failed to ensure device fingerprinting config: {e}')
        return None