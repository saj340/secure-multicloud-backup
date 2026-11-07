from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from . import twofactor
from .models import User


class RegistrationSerializer(serializers.Serializer):
    email = serializers.EmailField()
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password1 = serializers.CharField(write_only=True)
    password2 = serializers.CharField(write_only=True)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_password1(self, value):
        validate_password(value)
        return value

    def validate(self, data):
        if data["password1"] != data["password2"]:
            raise serializers.ValidationError({"password2": "Passwords do not match."})
        return data

    def save(self):
        return User.objects.create_user(
            email=self.validated_data["email"],
            password=self.validated_data["password1"],
            full_name=self.validated_data.get("full_name", ""),
        )


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    # Only needed when the account has two-factor login turned on.
    code = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=64)


class UserSerializer(serializers.ModelSerializer):
    two_factor_enabled = serializers.BooleanField(source="totp_enabled", read_only=True)
    backup_codes_left = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "email", "full_name", "date_joined", "two_factor_enabled", "backup_codes_left"]

    def get_backup_codes_left(self, user):
        return twofactor.backup_codes_left(user) if user.totp_enabled else 0


class TwoFactorPasswordSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)


class TwoFactorCodeSerializer(serializers.Serializer):
    code = serializers.CharField(write_only=True, max_length=64)


class TwoFactorPasswordAndCodeSerializer(TwoFactorPasswordSerializer, TwoFactorCodeSerializer):
    """Turning two-factor off or renewing backup codes needs both: who you are and the second factor."""


class ProfileUpdateSerializer(serializers.ModelSerializer):
    """Only the name is editable: the email is the login identity."""

    class Meta:
        model = User
        fields = ["full_name"]


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password1 = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate_new_password1(self, value):
        # Passing the user lets Django reject passwords too similar to their email or name.
        validate_password(value, user=self.context["request"].user)
        return value

    def validate(self, data):
        if data["new_password1"] != data["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Passwords do not match."})
        return data


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    code = serializers.CharField()
    new_password1 = serializers.CharField(write_only=True)
    new_password2 = serializers.CharField(write_only=True)

    def validate_new_password1(self, value):
        validate_password(value)
        return value

    def validate(self, data):
        if data["new_password1"] != data["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Passwords do not match."})
        return data
