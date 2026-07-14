from django.db import models
from .context import get_current_tenant


class TenantManager(models.Manager):
    def get_queryset(self):
        tenant_id = get_current_tenant()
        if tenant_id is None:
            # No tenant context set — fail SAFE, not open.
            # Returning .none() instead of unfiltered .all() ensures that
            # if middleware somehow didn't run (e.g. a management command,
            # a bug), we get an empty result, never another tenant's data.
            return super().get_queryset()
        return super().get_queryset().filter(tenant_id=tenant_id)