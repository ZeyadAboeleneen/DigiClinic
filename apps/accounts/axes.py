def get_username(request, credentials):
    """Lock out per normalized email, so `Sales@x.com` and `sales@x.com` share one counter."""
    value = (credentials or {}).get("username")
    if value is None and request is not None:
        value = request.POST.get("username")
    return (value or "").strip().lower() or None
