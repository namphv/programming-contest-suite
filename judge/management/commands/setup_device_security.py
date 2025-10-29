"""
Django management command to set up device security configuration.
"""

from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from judge.models import MiscConfig


class Command(BaseCommand):
    help = 'Set up device security configuration for current domain'

    def add_arguments(self, parser):
        parser.add_argument(
            '--enable',
            action='store_true',
            help='Enable device fingerprinting',
        )
        parser.add_argument(
            '--disable',
            action='store_true',
            help='Disable device fingerprinting',
        )
        parser.add_argument(
            '--domain',
            type=str,
            help='Specific domain to configure (optional)',
        )

    def handle(self, *args, **options):
        # Determine domain
        if options['domain']:
            domain = options['domain']
        else:
            try:
                current_site = Site.objects.get_current()
                domain = current_site.domain
            except:
                domain = 'localhost'
        
        # Determine action
        if options['enable']:
            value = 'true'
            action = 'enabled'
        elif options['disable']:
            value = 'false'
            action = 'disabled'
        else:
            # Just show current status
            self.show_status(domain)
            return
        
        # Set global config
        global_config, created = MiscConfig.objects.get_or_create(
            key='DEVICE_FINGERPRINTING_ENABLED',
            defaults={'value': value}
        )
        if not created:
            global_config.value = value
            global_config.save()
        
        # Set domain-specific config
        domain_key = f'{domain}:DEVICE_FINGERPRINTING_ENABLED'
        domain_config, created = MiscConfig.objects.get_or_create(
            key=domain_key,
            defaults={'value': value}
        )
        if not created:
            domain_config.value = value
            domain_config.save()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Device fingerprinting {action} for domain: {domain}'
            )
        )
        
        # Show final status
        self.show_status(domain)
    
    def show_status(self, domain):
        """Show current configuration status"""
        self.stdout.write(f'\n=== Device Security Configuration ===')
        self.stdout.write(f'Domain: {domain}')
        
        # Check global config
        try:
            global_config = MiscConfig.objects.get(key='DEVICE_FINGERPRINTING_ENABLED')
            self.stdout.write(f'Global: {global_config.value}')
        except MiscConfig.DoesNotExist:
            self.stdout.write('Global: not configured')
        
        # Check domain-specific config
        domain_key = f'{domain}:DEVICE_FINGERPRINTING_ENABLED'
        try:
            domain_config = MiscConfig.objects.get(key=domain_key)
            self.stdout.write(f'Domain-specific: {domain_config.value}')
        except MiscConfig.DoesNotExist:
            self.stdout.write('Domain-specific: not configured')
        
        # Test the actual function
        from django.test import RequestFactory
        from judge.utils.device_security import get_device_fingerprinting_enabled
        
        # Create a mock request
        factory = RequestFactory()
        request = factory.get('/')
        request.META['HTTP_HOST'] = domain
        
        try:
            enabled = get_device_fingerprinting_enabled(request)
            self.stdout.write(f'Effective status: {"enabled" if enabled else "disabled"}')
        except Exception as e:
            self.stdout.write(f'Error testing: {e}')
        
        self.stdout.write(f'===================================\n')