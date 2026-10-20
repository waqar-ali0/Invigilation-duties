from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def role_required(*roles):
    """Allow the view only for users with one of `roles` (superusers always pass).

    Anonymous users are sent to the login page; other users get a 403.
    """

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not (user.is_superuser or user.role in roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
