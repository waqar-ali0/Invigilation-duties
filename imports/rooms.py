"""Room inventory import: buildings and rooms, kept from one exam period to the next."""

from django.db import transaction

from audit.log import log
from exams.models import Building, Room, room_key

from .base import Field, ImportKind, Option, Plan, RowPlan, Status, norm, parse_count


class RoomImport(ImportKind):
    key = "rooms"
    title = "Rooms"
    description = (
        "The permanent room list: building, room, capacity and, if a room always needs a fixed "
        "number of invigilators, that number. Rooms are matched by building and room name."
    )
    fields = (
        Field("building", "Building", synonyms=("block", "building name", "location")),
        Field("campus", "Campus", synonyms=("site",)),
        Field("room", "Room", required=True, synonyms=("room no", "room number", "room name", "hall", "venue", "classroom", "lab")),
        Field("capacity", "Capacity", synonyms=("seats", "seating capacity", "students", "seating")),
        Field("invigilators", "Invigilators needed", synonyms=("invigilators", "required invigilators", "invigilators required", "staff needed", "faculty required")),
        Field("special", "Special requirements", synonyms=("requirements", "notes", "remarks", "special")),
    )
    options = (
        Option("default_building", "Building for rows without one", "text", default="Main Building"),
        Option("default_campus", "Campus for new buildings without one", "text", default=""),
    )

    def plan(self, batch):
        o = batch.options
        buildings = {norm(b.name): b for b in Building.objects.all()}
        rooms = {(r.building_id, room_key(r.name)): r for r in Room.objects.select_related("building")}
        seen, rows, new_buildings = {}, [], {}
        for number, cells in batch.rows:
            v = self.values(batch, cells)
            building_name = v["building"] or (o.get("default_building") or "").strip()
            p = RowPlan(number, v, label=f"{v['room']} — {building_name}" if v["room"] else f"Row {number}")
            p.parsed = {"building_name": building_name}
            if not v["room"]:
                p.errors.append("Room is missing")
            elif len(v["room"]) > 50:
                p.errors.append("Room name is longer than 50 characters")
            if not building_name:
                p.errors.append("Building is missing (give a building for rows without one)")
            elif len(building_name) > 100:
                p.errors.append("Building name is longer than 100 characters")
            for key, label, minimum in (("capacity", "Capacity", 0), ("invigilators", "Invigilators needed", 1)):
                try:
                    p.parsed[key] = parse_count(v[key], minimum)
                except ValueError:
                    p.errors.append(f"{label} must be a whole number{' of 1 or more' if minimum else ''}")
                    p.parsed[key] = None
            building = buildings.get(norm(building_name))
            if building is None and building_name:
                new_buildings.setdefault(norm(building_name), building_name)
            key = (norm(building_name), room_key(v["room"]))
            if v["room"] and building_name:
                if key in seen:
                    p.errors.append(f"Same room as row {seen[key]}")
                else:
                    seen[key] = number
            existing = rooms.get((building.pk, room_key(v["room"]))) if building else None
            if existing:
                p.match = existing
                p.changes = self._changes(existing, v, p.parsed)
                p.status = Status.UPDATE if p.changes else Status.SAME
            rows.append(p.finish())
        notes = ["Buildings that will be added: " + ", ".join(sorted(new_buildings.values()))] if new_buildings else []
        return Plan(rows, notes)

    def _changes(self, room, v, parsed):
        changes = []
        if parsed["capacity"] is not None and parsed["capacity"] != room.capacity:
            changes.append(("Capacity", room.capacity if room.capacity is not None else "—", parsed["capacity"]))
        if parsed["invigilators"] is not None and parsed["invigilators"] != room.invigilators_needed:
            changes.append(("Invigilators needed", room.invigilators_needed or "from student numbers", parsed["invigilators"]))
        if v["special"] and v["special"] != room.special_requirements:
            changes.append(("Special requirements", room.special_requirements or "—", v["special"]))
        if not room.is_active:
            changes.append(("Active", "no", "yes"))
        return changes

    def commit(self, batch, selected, decisions, mode, actor):
        counts = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "invalid": 0}
        campus = (batch.options.get("default_campus") or "").strip()
        with transaction.atomic():
            plan = self.plan(batch)
            for p in plan.rows:
                if p.status == Status.INVALID:
                    counts["invalid"] += 1
                elif p.number not in selected:
                    counts["skipped"] += 1
                elif p.status == Status.SAME:
                    counts["unchanged"] += 1
                elif p.status == Status.UPDATE:
                    if mode == "new_only":
                        counts["skipped"] += 1
                        continue
                    room = p.match
                    if p.parsed["capacity"] is not None:
                        room.capacity = p.parsed["capacity"]
                    if p.parsed["invigilators"] is not None:
                        room.invigilators_needed = p.parsed["invigilators"]
                    if p.values["special"]:
                        room.special_requirements = p.values["special"]
                    room.is_active = True
                    room.save()
                    counts["updated"] += 1
                else:
                    building = Building.objects.filter(name__iexact=p.parsed["building_name"]).first() or Building.objects.create(
                        name=p.parsed["building_name"], campus=p.values["campus"] or campus
                    )
                    Room.objects.create(
                        building=building,
                        name=" ".join(p.values["room"].split()),
                        capacity=p.parsed["capacity"],
                        invigilators_needed=p.parsed["invigilators"],
                        special_requirements=p.values["special"][:200],
                    )
                    counts["created"] += 1
            log(actor, "import.rooms", batch, after={"file": batch.filename, **counts})
        return counts
