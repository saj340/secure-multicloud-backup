from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=150, blank=True)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(auto_now_add=True)

    # Two-factor login (see twofactor.py). The secret is stored encrypted under the master
    # key, never in plain text; it is set when enrolment starts and only counts once enabled.
    totp_secret = models.TextField(blank=True)
    totp_enabled = models.BooleanField(default=False)
    # The newest 30-second time step already accepted, so a code can't be used twice.
    totp_last_step = models.BigIntegerField(default=0)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def __str__(self):
        return self.email


class BackupCode(models.Model):
    """A one-time code that replaces the authenticator app if the phone is lost."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="backup_codes")
    code_hash = models.CharField(max_length=64, help_text="A hash of the code, never the code itself.")
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"backup code for {self.user_id} ({'used' if self.used_at else 'unused'})"


class RateLimitCounter(models.Model):
    """Attempts counted against one identifier in its current window (see ratelimit.py)."""

    scope = models.CharField(max_length=64)
    key = models.CharField(max_length=64, help_text="A hash of the identifier, never the identifier itself.")
    count = models.PositiveIntegerField(default=0)
    window_started = models.DateTimeField()

    class Meta:
        unique_together = ["scope", "key"]

    def __str__(self):
        return f"{self.scope}: {self.count}"
