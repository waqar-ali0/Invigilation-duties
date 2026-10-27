"""Room inventory pages: buildings and rooms kept from one exam period to the next."""

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Role
from accounts.permissions import role_required
from audit.log import log

from .models import Building, Room

admin_only = role_required(Role.ADMIN)


class RoomForm(forms.ModelForm):
    class Meta:
        model = Room
        fields = ["building", "name", "capacity", "invigilators_needed", "special_requirements", "is_active"]
        labels = {"is_active": "In use"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["building"].queryset = Building.objects.filter(Q(is_active=True) | Q(pk=self.instance.building_id))
        self.fields["building"].help_text = "Not listed? Add the building on the Rooms page first."

    def clean_name(self):
        return " ".join(self.cleaned_data["name"].split())


class BuildingForm(forms.ModelForm):
    class Meta:
        model = Building
        fields = ["name", "campus"]

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if Building.objects.filter(name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This building already exists.")
        return name


@admin_only
def room_list(request):
    building_form = BuildingForm(request.POST or None, prefix="b")
    if request.method == "POST" and building_form.is_valid():
        b = building_form.save()
        log(request.user, "building.create", b, after={"name": b.name, "campus": b.campus})
        messages.success(request, f"Building {b} added. You can now add its rooms.")
        return redirect("room_list")
    qs = Room.objects.select_related("building").annotate(exams=Count("allocations")).order_by("building__name", "name")
    q = request.GET.get("q", "").strip()
    building = request.GET.get("building", "")
    show = request.GET.get("show", "active")
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(building__name__icontains=q) | Q(building__campus__icontains=q))
    if building.isdigit():
        qs = qs.filter(building_id=building)
    if show == "active":
        qs = qs.filter(is_active=True)
    elif show == "inactive":
        qs = qs.filter(is_active=False)
    params = request.GET.copy()
    params.pop("page", None)
    context = {
        "page": Paginator(qs, 50).get_page(request.GET.get("page")),
        "q": q,
        "building": building,
        "show": show,
        "buildings": Building.objects.annotate(n=Count("rooms")),
        "querystring": params.urlencode(),
        "total": Room.objects.filter(is_active=True).count(),
        "building_form": building_form,
    }
    return render(request, "exams/rooms.html", context)


def _room_form(request, room=None):
    form = RoomForm(request.POST or None, instance=room)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        log(request.user, "room.update" if room else "room.create", saved, after={"changed": form.changed_data})
        messages.success(request, f"Room {saved} saved.")
        return redirect("room_list")
    title = f"Edit room {room}" if room else "Add a room"
    return render(request, "exams/room_form.html", {"form": form, "title": title, "room": room})


@admin_only
def room_create(request):
    return _room_form(request)


@admin_only
def room_edit(request, pk):
    return _room_form(request, get_object_or_404(Room, pk=pk))
