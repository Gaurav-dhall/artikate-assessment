from contextvars import ContextVar

_current_tenant_id: ContextVar[int | None] = ContextVar("current_tenant_id", default=None)


def set_current_tenant(tenant_id):
    _current_tenant_id.set(tenant_id)


def get_current_tenant():
    return _current_tenant_id.get()


def clear_current_tenant():
    _current_tenant_id.set(None)