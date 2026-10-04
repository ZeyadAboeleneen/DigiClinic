from django.db import models


class BaseModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrgQuerySet(models.QuerySet):
    def for_org(self, organization):
        if organization is None:
            return self.none()
        return self.filter(organization=organization)


class TenantScopedModel(BaseModel):
    """Every operational model inherits this. Views must query via `.for_org(request.organization)`."""

    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.CASCADE, related_name="+", db_index=True
    )

    objects = OrgQuerySet.as_manager()

    class Meta:
        abstract = True
