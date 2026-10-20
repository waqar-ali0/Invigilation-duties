from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from .models import Role


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


def _linked_faculty(user):
    from faculty.models import Faculty

    return Faculty.objects.filter(user=user).select_related("department").first()


def faculty_required(view):
    """Faculty and HOD pages: the user must be linked to a faculty record (set as request.faculty)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if user.role not in (Role.FACULTY, Role.HOD):
            raise PermissionDenied
        request.faculty = _linked_faculty(user)
        if request.faculty is None:
            return render(request, "accounts/not_linked.html")
        return view(request, *args, **kwargs)

    return wrapper


def hod_required(view):
    """HOD pages: like faculty_required, plus request.hod_departments (departments they head)."""

    @wraps(view)
    @faculty_required
    def wrapper(request, *args, **kwargs):
        from faculty.models import Department

        request.hod_departments = Department.objects.filter(hod=request.faculty)
        if request.user.role != Role.HOD or not request.hod_departments.exists():
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper
