from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class ExpiringTokenAuthentication(TokenAuthentication):
    """Token authentication where a token stops working AUTH_TOKEN_LIFETIME after it was issued."""

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if timezone.now() - token.created > settings.AUTH_TOKEN_LIFETIME:
            token.delete()
            raise AuthenticationFailed("Your session has expired. Please log in again.")
        return user, token
