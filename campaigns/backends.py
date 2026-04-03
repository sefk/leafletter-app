"""
Custom authentication backends for Leafletter.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """
    Authenticate with either username or email address.

    Tries exact username match first, then falls back to a case-insensitive
    email lookup.  Password validation and active-user checks are inherited
    from ModelBackend.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()

        # Try username first (exact match, same as ModelBackend default)
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            # Fall back to case-insensitive email lookup
            try:
                user = User.objects.get(email__iexact=username)
            except User.DoesNotExist:
                # Run the default password hasher to mitigate timing attacks
                User().set_password(password)
                return None
            except User.MultipleObjectsReturned:
                # Multiple accounts share the same email — refuse ambiguous login
                return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
