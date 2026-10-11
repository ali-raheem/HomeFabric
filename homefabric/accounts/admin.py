from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from homefabric.accounts.models import Household, Invitation, LoginAttempt, Membership, User


@admin.register(User)
class FabricUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("HomeFabric", {"fields": ("display_name",)}),)
    list_display = ("username", "display_name", "email", "is_active", "is_superuser", "last_login")


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Household)
class HouseholdAdmin(admin.ModelAdmin):
    list_display = ("name", "timezone", "currency", "created_at")
    inlines = [MembershipInline]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("household", "email", "role", "created_at", "expires_at", "used_at")
    exclude = ("token_hash",)
    readonly_fields = ("used_at", "used_by")


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("username", "ip_address", "succeeded", "timestamp")
    list_filter = ("succeeded",)
