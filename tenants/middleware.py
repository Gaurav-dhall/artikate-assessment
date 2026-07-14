from django.http import JsonResponse
from .models import Tenant
from .context import set_current_tenant, clear_current_tenant


class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tenant_id = self._resolve_tenant(request)

        if tenant_id is None:
            return JsonResponse({"error": "Tenant could not be identified"}, status=400)

        set_current_tenant(tenant_id)
        try:
            response = self.get_response(request)
        finally:
            # CRITICAL: always clear, even if the view raised an exception.
            # Without this, in a threaded/worker-reused environment, the
            # next unrelated request handled by this same worker could
            # accidentally inherit this tenant's context.
            clear_current_tenant()

        return response

    def _resolve_tenant(self, request):
        # Approach 1: subdomain, e.g. nike.yourapp.com
        host = request.get_host().split(':')[0]
        subdomain = host.split('.')[0]
        tenant = Tenant.objects.filter(subdomain=subdomain).first()
        if tenant:
            return tenant.id

        # Approach 2 (fallback for local dev/testing): explicit header
        header_value = request.headers.get('X-Tenant-ID')
        if header_value:
            return int(header_value)

        return None