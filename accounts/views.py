import math

from django.contrib.auth import authenticate
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from monitoring.models import ActivityLog
from monitoring.service import log_activity

from . import ratelimit
from .models import User
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileUpdateSerializer,
    RegistrationSerializer,
    UserSerializer,
)


def _client_ip(request) -> str:
    # Behind a reverse proxy this is the proxy's address; see the README.
    return request.META.get("REMOTE_ADDR", "unknown")


def _too_many(seconds: int, what: str) -> Response:
    minutes = max(1, math.ceil(seconds / 60))
    response = Response(
        {"detail": f"Too many {what}. Try again in {minutes} minute{'s' if minutes != 1 else ''}."},
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = str(seconds)
    return response


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ip = _client_ip(request)
        if wait := ratelimit.blocked_for(("register_ip", ip)):
            return _too_many(wait, "sign-up attempts")
        ratelimit.record("register_ip", ip)

        serializer = RegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        log_activity(user, "registered", "Account created.")
        return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ip = _client_ip(request)
        email = serializer.validated_data["email"].lower()
        pair = f"{ip}|{email}"

        # Checked before the password is, so a blocked guesser can't tell whether
        # a guess was right.
        if wait := ratelimit.blocked_for(("login_account", pair), ("login_ip", ip)):
            return _too_many(wait, "failed login attempts")

        user = authenticate(request, username=email, password=serializer.validated_data["password"])
        if user is None:
            # An unknown email counts and answers exactly like a wrong password.
            ratelimit.record("login_account", pair)
            ratelimit.record("login_ip", ip)
            # Logged the same way whether or not the account exists, so this path
            # takes the same time either way and can't be used to find accounts.
            log_activity(
                User.objects.filter(email=email).first(),
                "login_failed",
                "Failed login attempt.",
                level=ActivityLog.Level.WARNING,
                email=email,
                ip=ip,
            )
            return Response(
                {"detail": "Invalid email or password."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        ratelimit.reset("login_account", pair)
        # A fresh token every login, so its lifetime starts now and any earlier
        # session (another device, a stolen token) ends.
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        log_activity(user, "login", "Signed in.", ip=ip)
        return Response({"token": token.key, "user": UserSerializer(user).data})


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        log_activity(request.user, "logout", "Signed out.")
        request.user.auth_token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = ProfileUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class ChangePasswordView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        who = str(user.pk)
        # Someone holding a stolen token could otherwise guess the current password here.
        if wait := ratelimit.blocked_for(("change_password_user", who)):
            return _too_many(wait, "failed attempts")

        serializer = ChangePasswordSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        if not user.check_password(serializer.validated_data["current_password"]):
            ratelimit.record("change_password_user", who)
            return Response(
                {"detail": "Your current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ratelimit.reset("change_password_user", who)
        user.set_password(serializer.validated_data["new_password1"])
        user.save()

        # Whoever else was signed in (or holds a stolen token) is signed out; this
        # client gets a fresh token so it stays signed in.
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        log_activity(user, "password_changed", "Password changed; other sessions were signed out.")
        return Response({"token": token.key})


class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"].lower()
        ip = _client_ip(request)

        # Every request counts, whether or not the account exists, so the limit
        # protects inboxes from being flooded without revealing who has an account.
        checks = (("reset_request_email", email), ("reset_request_ip", ip))
        if wait := ratelimit.blocked_for(*checks):
            return _too_many(wait, "reset requests")
        for scope, identifier in checks:
            ratelimit.record(scope, identifier)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            user = None

        if user is not None:
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
            token = default_token_generator.make_token(user)
            code = f"{uidb64}:{token}"
            send_mail(
                subject="Password reset code",
                message=(
                    "Use this code in the app to reset your password:\n\n"
                    f"{code}\n\n"
                    "This code expires after a limited time and can only be used once."
                ),
                from_email=None,
                recipient_list=[user.email],
            )

        # Always return the same response, whether or not the account exists,
        # so this endpoint can't be used to check which emails are registered.
        return Response(
            {"detail": "If that email is registered, a reset code has been sent."}
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        ip = _client_ip(request)
        if wait := ratelimit.blocked_for(("reset_confirm_ip", ip)):
            return _too_many(wait, "failed attempts")

        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        def refuse():
            ratelimit.record("reset_confirm_ip", ip)
            return Response(
                {"detail": "Invalid or expired reset code."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            uidb64, token = serializer.validated_data["code"].split(":", 1)
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (ValueError, TypeError, User.DoesNotExist):
            return refuse()

        if not default_token_generator.check_token(user, token):
            return refuse()

        user.set_password(serializer.validated_data["new_password1"])
        user.save()

        # Resetting the password invalidates any existing session for this
        # user, in case the old token had been compromised.
        Token.objects.filter(user=user).delete()
        log_activity(user, "password_reset", "Password reset with an emailed code.")

        return Response({"detail": "Password has been reset. You can now log in."})
