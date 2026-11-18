"""Authentication and permission classes for the Wenqu customer API.

``WenquJWTAuthentication`` extends SimpleJWT's JWTAuthentication with the
helpdesk ``tv`` (token_version) check: a customer access token whose ``tv`` claim
no longer matches ``WenquProfile.token_version`` is rejected, so bumping the
column (e.g. on a permission change or forced sign-out) actually ends old
sessions. Staff tokens carry no ``tv`` and are never subject to this check, and
customer tokens without a ``tv`` claim are passed through unchanged - the check
only fires when the claim is present.

``WenquCustomerPermission`` gates on the *presence* of a wenqu_profile, which is
what distinguishes an external customer from a staff user. The finer-grained
``CanChat`` / ``CanUpload`` subclasses additionally honour the profile flags;
business views (PR4) pick the one they need.
"""
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class WenquJWTAuthentication(JWTAuthentication):
    """JWT auth that additionally enforces the customer ``tv`` claim."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result
        profile = getattr(user, 'wenqu_profile', None)
        if profile is not None and 'tv' in validated_token:
            if validated_token['tv'] != profile.token_version:
                raise AuthenticationFailed(
                    _('Token has been invalidated. Please sign in again.'),
                    code='token_version_mismatch',
                )
        return user, validated_token


class WenquCustomerPermission(BasePermission):
    """Allow only authenticated users that have a Wenqu customer profile."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'wenqu_profile', None) is not None
        )


class CanChat(WenquCustomerPermission):
    """Customer profile present AND chat enabled."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.wenqu_profile.can_chat


class CanUpload(WenquCustomerPermission):
    """Customer profile present AND uploads enabled."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.wenqu_profile.can_upload