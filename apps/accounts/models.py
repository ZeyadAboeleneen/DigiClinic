import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import TenantScopedModel
from apps.organizations.models import Role


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    """Login is by email. `username` is removed."""

    username = None
    first_name = None
    last_name = None
    email = models.EmailField(_("الإيميل"), unique=True)
    full_name = models.CharField(_("الاسم"), max_length=150)
    phone = models.CharField(_("الموبايل"), max_length=20, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    objects = UserManager()

    class Meta:
        verbose_name = _("مستخدم")
        verbose_name_plural = _("المستخدمين")

    def __str__(self):
        return self.full_name or self.email

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InvitationQuerySet(models.QuerySet):
    def for_org(self, organization):
        if organization is None:
            return self.none()
        return self.filter(organization=organization)

    def pending(self):
        return self.filter(accepted_at__isnull=True, revoked_at__isnull=True, expires_at__gt=timezone.now())


class Invitation(TenantScopedModel):
    """One-time invite link, valid 48h. Only the token hash is stored."""

    email = models.EmailField(_("الإيميل"))
    full_name = models.CharField(_("الاسم"), max_length=150)
    role = models.CharField(_("الدور"), max_length=20, choices=Role.choices, default=Role.SALES)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    accepted_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    objects = InvitationQuerySet.as_manager()

    class Meta:
        verbose_name = _("دعوة")
        verbose_name_plural = _("الدعوات")

    def __str__(self):
        return f"{self.email} → {self.organization}"

    @classmethod
    def create_for(cls, *, organization, email, full_name, role, invited_by):
        token = secrets.token_urlsafe(32)
        inv = cls.objects.create(
            organization=organization,
            email=email.lower(),
            full_name=full_name,
            role=role,
            token_hash=hash_token(token),
            expires_at=timezone.now() + timedelta(hours=settings.INVITATION_VALID_HOURS),
            invited_by=invited_by,
        )
        return inv, token

    @classmethod
    def from_token(cls, token: str):
        return cls.objects.pending().select_related("organization").filter(token_hash=hash_token(token)).first()

    @property
    def is_pending(self):
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > timezone.now()
