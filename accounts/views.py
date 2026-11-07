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

from . import ratelimit, twofactor
from .models import User
from .serializers import (
    ChangePasswordSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    ProfileUpdateSerializer,
    RegistrationSerializer,
    TwoFactorCodeSerializer,
    TwoFactorPasswordAndCodeSerializer,
    TwoFactorPasswordSerializer,
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

        method = None
        if user.totp_enabled:
            code = serializer.validated_data.get("code", "").strip()
            if not code:
                # The password was right but that is only the first factor: no session yet.
                return Response(
                    {"detail": "Enter the code from your authenticator app.", "two_factor_required": True},
                    status=status.HTTP_401_UNAUTHORIZED,
                )
            who = str(user.pk)
            # Whoever got this far knows the password, so wrong codes are counted per account
            # as well as per IP: guessing a 6-digit code from many addresses still runs out.
            if wait := ratelimit.blocked_for(("login_code_user", who)):
                return _too_many(wait, "wrong verification codes")
            method = twofactor.verify_second_factor(user, code)
            if method is None:
                ratelimit.record("login_code_user", who)
                ratelimit.record("login_account", pair)
                ratelimit.record("login_ip", ip)
                log_activity(
                    user, "login_failed", "Failed two-factor code.",
                    level=ActivityLog.Level.WARNING, ip=ip,
                )
                return Response(
                    {"detail": "Invalid verification code."}, status=status.HTTP_401_UNAUTHORIZED
                )
            ratelimit.reset("login_code_user", who)

        ratelimit.reset("login_account", pair)
        # A fresh token every login, so its lifetime starts now and any earlier
        # session (another device, a stolen token) ends.
        Token.objects.filter(user=user).delete()
        token = Token.objects.create(user=user)
        if method == "backup":
            log_activity(
                user, "backup_code_used",
                f"Signed in with a backup code; {twofactor.backup_codes_left(user)} left.",
                level=ActivityLog.Level.WARNING, ip=ip,
            )
        else:
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


def _wrong(message: str) -> Response:
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


class TwoFactorSetupView(APIView):
    """Step 1 of turning two-factor on: a new secret for the authenticator app (not active yet)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        who = str(user.pk)
        # A stolen session token must not be enough to enrol someone else's authenticator.
        if wait := ratelimit.blocked_for(("two_factor_user", who)):
            return _too_many(wait, "failed attempts")
        serializer = TwoFactorPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not user.check_password(serializer.validated_data["password"]):
            ratelimit.record("two_factor_user", who)
            return _wrong("Your password is incorrect.")
        if user.totp_enabled:
            return _wrong("Two-factor login is already turned on.")
        secret, uri = twofactor.begin_enrolment(user)
        return Response({"secret": secret, "otpauth_uri": uri})


class TwoFactorEnableView(APIView):
    """Step 2: a code from the app proves it was set up correctly; then two-factor turns on."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        who = str(user.pk)
        if wait := ratelimit.blocked_for(("two_factor_user", who)):
            return _too_many(wait, "failed attempts")
        serializer = TwoFactorCodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if not twofactor.has_pending_enrolment(user):
            return _wrong("Start the setup first.")
        if not twofactor.verify_totp(user, serializer.validated_data["code"]):
            ratelimit.record("two_factor_user", who)
            return _wrong("That code is not correct. Check the code in your app and try again.")
        ratelimit.reset("two_factor_user", who)
        user.totp_enabled = True
        user.save(update_fields=["totp_enabled"])
        codes = twofactor.generate_backup_codes(user)
        log_activity(user, "two_factor_enabled", "Two-factor login turned on.")
        return Response({"backup_codes": codes})


def _confirm_identity(request):
    """For sensitive changes: the password and a current code. Returns (user, error response)."""
    user = request.user
    who = str(user.pk)
    if wait := ratelimit.blocked_for(("two_factor_user", who)):
        return None, _too_many(wait, "failed attempts")
    serializer = TwoFactorPasswordAndCodeSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    if not user.totp_enabled:
        return None, _wrong("Two-factor login is not turned on.")
    if not user.check_password(serializer.validated_data["password"]):
        ratelimit.record("two_factor_user", who)
        return None, _wrong("Your password is incorrect.")
    if twofactor.verify_second_factor(user, serializer.validated_data["code"]) is None:
        ratelimit.record("two_factor_user", who)
        return None, _wrong("That code is not correct.")
    ratelimit.reset("two_factor_user", who)
    return user, None


class TwoFactorDisableView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user, error = _confirm_identity(request)
        if error:
            return error
        twofactor.turn_off(user)
        log_activity(
            user, "two_factor_disabled", "Two-factor login turned off.", level=ActivityLog.Level.WARNING
        )
        return Response({"detail": "Two-factor login is turned off."})


class BackupCodesRenewView(APIView):
    """Replace the backup codes, e.g. after using some. The old ones stop working."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user, error = _confirm_identity(request)
        if error:
            return error
        codes = twofactor.generate_backup_codes(user)
        log_activity(user, "backup_codes_renewed", "New backup codes were created.")
        return Response({"backup_codes": codes})


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
