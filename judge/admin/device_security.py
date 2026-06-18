from django.contrib import admin
from django.contrib.admin.views.main import ChangeList
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import path
from django.utils.html import format_html
from django.utils.translation import gettext as _, ngettext
from django.conf import settings

from judge.models import Profile


class DeviceSecurityChangeList(ChangeList):
    """Custom changelist for device security management"""
    
    def get_queryset(self, request):
        # Filter to show only regular users (non-staff, non-superuser)
        qs = super().get_queryset(request)
        return qs.filter(
            user__is_staff=False, 
            user__is_superuser=False
        ).select_related('user')


class DeviceSecurityAdmin(admin.ModelAdmin):
    """Custom admin for device security management"""
    
    model = Profile
    
    def _get_fingerprinting_enabled(self, request):
        """Get fingerprinting enabled status from admin settings"""
        try:
            from judge.models import MiscConfig
            config = MiscConfig.objects.get(key='DEVICE_FINGERPRINTING_ENABLED')
            return config.value.lower() == 'true'
        except:
            # Default to enabled if not configured
            return True
    list_display = [
        'username',
        'email',
        'device_security_status',
        'device_security_exempt',
        'device_registered_date',
        'last_access',
        'device_actions'
    ]
    list_filter = [
        ('device_fingerprint', admin.EmptyFieldListFilter),
        'device_security_exempt',
        'device_registered_at',
        'last_access'
    ]
    list_editable = ['device_security_exempt']
    search_fields = ['user__username', 'user__email', 'device_id']
    actions = ['reset_selected_devices', 'bulk_reset_all_devices', 'exempt_selected_users', 'unexempt_selected_users']
    
    def get_actions(self, request):
        """Remove default delete action"""
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions
    
    def has_delete_permission(self, request, obj=None):
        """Disable delete permission for device security management"""
        return False
    
    def get_changelist(self, request, **kwargs):
        return DeviceSecurityChangeList
    
    def get_queryset(self, request):
        # Only show regular users (non-admin)
        return super().get_queryset(request).filter(
            user__is_staff=False,
            user__is_superuser=False
        ).select_related('user')
    
    def username(self, obj):
        return obj.user.username
    username.short_description = _('Username')
    username.admin_order_field = 'user__username'
    
    def email(self, obj):
        return obj.user.email
    email.short_description = _('Email')
    email.admin_order_field = 'user__email'
    
    def device_security_status(self, obj):
        if obj.device_fingerprint:
            return format_html(
                '<span style="color: green; font-weight: bold;">✓ Secured</span>'
            )
        else:
            return format_html(
                '<span style="color: orange;">⚠ No Device</span>'
            )
    device_security_status.short_description = _('Security Status')
    
    def device_registered_date(self, obj):
        if obj.device_registered_at:
            return obj.device_registered_at.strftime('%Y-%m-%d %H:%M')
        return '-'
    device_registered_date.short_description = _('Device Registered')
    device_registered_date.admin_order_field = 'device_registered_at'
    
    def device_actions(self, obj):
        if obj.device_fingerprint:
            return format_html(
                '<a href="#" onclick="if(confirm(\'Reset device security for {}?\')) {{ '
                'window.location.href=\'/admin/device-security/reset/{}/\'; }}">'
                '<span style="color: red;">Reset Device</span></a>',
                obj.user.username, obj.id
            )
        else:
            return format_html('<span style="color: gray;">No Action</span>')
    device_actions.short_description = _('Actions')
    
    def reset_selected_devices(self, request, queryset):
        """Reset device security for selected users"""
        count = 0
        for profile in queryset:
            if profile.device_fingerprint:
                profile.device_fingerprint = None
                profile.device_id = None
                profile.device_registered_at = None
                profile.save(update_fields=['device_fingerprint', 'device_id', 'device_registered_at'])
                count += 1
        
        self.message_user(
            request,
            ngettext(
                "%d user device security was reset.",
                "%d users device security was reset.", 
                count,
            ) % count,
        )
    reset_selected_devices.short_description = _("Reset device security for selected users")
    
    def bulk_reset_all_devices(self, request, queryset):
        """Bulk reset all device security"""
        updated = queryset.update(
            device_fingerprint=None,
            device_id=None,
            device_registered_at=None
        )
        
        self.message_user(
            request,
            ngettext(
                "%d user device security was reset (bulk).",
                "%d users device security was reset (bulk).",
                updated,
            ) % updated,
        )
    bulk_reset_all_devices.short_description = _("Bulk reset ALL device security")

    def exempt_selected_users(self, request, queryset):
        updated = queryset.update(device_security_exempt=True)
        self.message_user(
            request,
            ngettext(
                "%d user was exempted from device security.",
                "%d users were exempted from device security.",
                updated,
            ) % updated,
        )
    exempt_selected_users.short_description = _("Exempt selected users from device security")

    def unexempt_selected_users(self, request, queryset):
        updated = queryset.update(device_security_exempt=False)
        self.message_user(
            request,
            ngettext(
                "%d user device security exemption was removed.",
                "%d users device security exemption was removed.",
                updated,
            ) % updated,
        )
    unexempt_selected_users.short_description = _("Remove device security exemption for selected users")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('statistics/', self.admin_site.admin_view(self.device_statistics_view), name='device_statistics'),
            path('reset/<int:profile_id>/', self.admin_site.admin_view(self.reset_single_device), name='reset_single_device'),
        ]
        return custom_urls + urls
    
    def device_statistics_view(self, request):
        """Device security statistics dashboard"""
        stats = {
            'total_users': Profile.objects.filter(user__is_staff=False, user__is_superuser=False).count(),
            'secured_users': Profile.objects.filter(
                user__is_staff=False, user__is_superuser=False, device_fingerprint__isnull=False
            ).count(),
            'unsecured_users': Profile.objects.filter(
                user__is_staff=False, user__is_superuser=False, device_fingerprint__isnull=True
            ).count(),
            'fingerprinting_enabled': self._get_fingerprinting_enabled(request),
            'enforce_mode': getattr(settings, 'DEVICE_FINGERPRINTING_ENFORCE_MODE', True),
        }
        
        stats['security_coverage'] = (
            (stats['secured_users'] / stats['total_users'] * 100) 
            if stats['total_users'] > 0 else 0
        )
        
        return render(request, 'admin/device_security_stats.html', {
            'title': _('Device Security Statistics'),
            'stats': stats,
        })
    
    def reset_single_device(self, request, profile_id):
        """Reset device security for a single user"""
        try:
            profile = Profile.objects.get(id=profile_id, user__is_staff=False, user__is_superuser=False)
            if profile.device_fingerprint:
                profile.device_fingerprint = None
                profile.device_id = None
                profile.device_registered_at = None
                profile.save(update_fields=['device_fingerprint', 'device_id', 'device_registered_at'])
                self.message_user(request, _('Device security reset for user: %s') % profile.user.username)
            else:
                self.message_user(request, _('User %s has no device registered') % profile.user.username)
        except Profile.DoesNotExist:
            self.message_user(request, _('User not found'), level='error')
        
        return HttpResponse('<script>window.history.back();</script>')


# Register the custom admin view
# This will be registered in the main admin.py file