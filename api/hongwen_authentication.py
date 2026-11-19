"""Authentication and permission classes for the Hongwen customer API.

``HongwenJWTAuthentication`` extends SimpleJWT's JWTAuthentication with the
helpdesk ``tv`` (token_version) check: a customer access token whose ``tv`` claim
no longer matches ``HongwenProfile.token_version`` is rejected, so bumping the
column (e.g. on a permission change or forced sign-out) actually ends old
sessions. Staff tokens carry no ``tv`` and are never subject to this check, and
customer tokens without a ``tv`` claim are passed through unchanged - the check
only fires when the claim is present.

``HongwenCustomerPermission`` gates on the *presence* of a hongwen_profile, which is
what distinguishes an external customer from a staff user. The finer-grained
``CanChat`` / ``CanUpload`` subclasses additionally honour the profile flags;
business views (PR4) pick the one they need. ``HongwenQueryTokenAuthentication``
adds ``?access_token=`` support for media playback routes (WeChat audio APIs
cannot set headers) while keeping the same tv enforcement.
"""
from django.utils.translation import gettext_lazy as _
from rest_framework.permissions import BasePermission
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed


class HongwenJWTAuthentication(JWTAuthentication):
    """JWT auth that additionally enforces the customer ``tv`` claim."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, validated_token = result
        profile = getattr(user, 'hongwen_profile', None)
        if profile is not None and 'tv' in validated_token:
            if validated_token['tv'] != profile.token_version:
                raise AuthenticationFailed(
                    _('Token has been invalidated. Please sign in again.'),
                    code='token_version_mismatch',
                )
        return user, validated_token


class HongwenQueryTokenAuthentication(HongwenJWTAuthentication):
    """Bearer auth for media routes that cannot set an Authorization header.

    WeChat's audio playback APIs accept a URL only, so the client appends
    ``?access_token=<jwt>`` (helpdesk app/auth.py:126 does the same). Synthesizing
    the bearer header from the query parameter reuses the parent's entire
    validation path - signature, expiry, user lookup AND the customer ``tv``
    check - so a query token is exactly as revocable as a header token. A real
    Authorization header always wins when both are present.
    """

    def get_header(self, request):
        header = super().get_header(request)
        if header is None:
            token = str(request.query_params.get('access_token') or '').strip()
            if token:
                return f'Bearer {token}'.encode('utf-8')
        return header


class HongwenCustomerPermission(BasePermission):
    """Allow only authenticated users that have a Hongwen customer profile."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, 'hongwen_profile', None) is not None
        )


class CanChat(HongwenCustomerPermission):
    """Customer profile present AND chat enabled."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.hongwen_profile.can_chat


class CanUpload(HongwenCustomerPermission):
    """Customer profile present AND uploads enabled."""

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.user.hongwen_profile.can_upload