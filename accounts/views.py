from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from django.views.decorators.http import require_POST

from faculty.models import Faculty

from .invitations import InvitationError, invite, invite_all
from .models import Role, User
from .permissions import role_required

admin_only = role_required(Role.ADMIN)


@admin_only
def accounts_page(request):
    groups = {"none": [], "invited": [], "active": []}
    for f in Faculty.objects.filter(is_active=True).select_related("department", "user"):
        if f.user is None:
            groups["none"].append(f)
        elif f.user.has_usable_password():
            groups["active"].append(f)
        else:
            groups["invited"].append(f)
    can_invite = sum(1 for f in groups["none"] if f.email and f.invited_at is None)
    return render(request, "accounts/accounts_page.html", {"groups": groups, "can_invite": can_invite})


@admin_only
@require_POST
def invite_all_view(request):
    count = invite_all(request.user)
    messages.success(request, f"{count} activation email(s) queued.")
    return redirect("accounts_page")


@admin_only
@require_POST
def invite_one_view(request, faculty_id):
    faculty = get_object_or_404(Faculty, pk=faculty_id)
    try:
        invite(faculty, request.user)
    except InvitationError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, f"Activation email queued for {faculty.name}.")
    return redirect("accounts_page")


def _user_from_link(uidb64, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    return user if default_token_generator.check_token(user, token) else None


def activate(request, uidb64, token):
    """Set a password from an activation link, then log in."""
    user = _user_from_link(uidb64, token)
    if user is None or not user.is_active:
        return render(request, "accounts/activate.html", {"invalid": True})
    form = SetPasswordForm(user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        login(request, user)
        messages.success(request, "Your account is ready.")
        return redirect("home")
    return render(request, "accounts/activate.html", {"form": form, "account": user})
