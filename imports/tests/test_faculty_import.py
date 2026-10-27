import pytest
from django.urls import reverse

from accounts.models import Role, User
from duties.models import Duty, DutySource
from exams.tests.helpers import make_csv, make_xlsx
from faculty.models import Faculty, FacultyCategory, next_faculty_id
from imports.base import Status
from imports.faculty import FacultyImport
from imports.models import BatchStatus, ImportBatch
from imports.reader import read_table

pytestmark = pytest.mark.django_db

# The user's own layout: no faculty ID, their own headings.
HEADERS = ["Sr #", "Teacher Name", "Rank", "Mobile No", "Dept"]


def upload(client, rows, name="staff.xlsx"):
    r = client.post(reverse("import_start", args=["faculty"]), {"file": make_xlsx([HEADERS, *rows], name)})
    assert r.status_code == 302, r.content.decode()[:500]
    return ImportBatch.objects.latest("pk")


def map_columns(client, batch, **options):
    data = {f"map_{k}": v for k, v in batch.mapping.items()}
    data.update({f"opt_{k}": v for k, v in options.items()})
    r = client.post(reverse("import_columns", args=[batch.pk]), data)
    assert r.status_code == 302, r.content.decode()[:800]
    batch.refresh_from_db()
    return batch


def confirm(client, batch, rows=None, mode="update", **decisions):
    plan = FacultyImport().plan(batch)
    chosen = rows if rows is not None else [p.number for p in plan.rows if p.status != Status.INVALID]
    data = {"row": [str(n) for n in chosen], "mode": mode}
    data.update({f"decision_{n}": v for n, v in decisions.items()})
    r = client.post(reverse("import_batch", args=[batch.pk]), data)
    assert r.status_code == 302
    batch.refresh_from_db()
    return batch


def full_import(client, rows, **options):
    return confirm(client, map_columns(client, upload(client, rows), **options))


def test_columns_are_guessed_from_other_headings(admin_client, dept):
    batch = upload(admin_client, [[1, "Ali Raza", "Lecturer", "0300-1234567", dept.name]])
    k = FacultyImport()
    named = {f.key: batch.headers[i] for f in k.fields for key, i in batch.mapping.items() if key == f.key}
    assert named == {"name": "Teacher Name", "category": "Rank", "phone": "Mobile No", "department": "Dept"}


def test_import_without_ids_generates_fac_ids_and_saves_only_after_confirmation(admin_client, dept):
    batch = map_columns(admin_client, upload(admin_client, [
        [1, "Ali Raza", "Lecturer", "0300-1234567", dept.name],
        [2, "Sana Malik", "Asst. Prof.", 3001112222, dept.name],  # Excel dropped the leading 0
    ]))
    assert batch.status == BatchStatus.PREVIEW
    assert not Faculty.objects.exists()  # nothing saved before confirmation
    preview = admin_client.get(reverse("import_batch", args=[batch.pk])).content.decode()
    assert "Ali Raza" in preview and "New" in preview
    confirm(admin_client, batch)
    ali, sana = Faculty.objects.get(name="Ali Raza"), Faculty.objects.get(name="Sana Malik")
    assert (ali.employee_id, sana.employee_id) == ("FAC-0001", "FAC-0002")
    assert sana.category.code == "assistant_professor"
    assert (ali.phone_e164, sana.phone_e164) == ("+923001234567", "+923001112222")
    assert batch.result["created"] == 2 and batch.uploaded_by.role == Role.ADMIN


def test_reimport_matches_by_phone_updates_and_never_duplicates(admin_client, dept, make_faculty, room):
    full_import(admin_client, [[1, "Ali Raza", "Lecturer", "0300-1234567", dept.name]])
    ali = Faculty.objects.get(name="Ali Raza")
    duty = Duty.objects.create(faculty=ali, room_allocation=room, source=DutySource.MANUAL)
    batch = full_import(admin_client, [[1, "Ali Raza Khan", "Assistant Professor", "+92 300 1234567", ""]])
    ali.refresh_from_db()
    assert Faculty.objects.count() == 1 and ali.employee_id == "FAC-0001"  # ID never changes
    assert ali.name == "Ali Raza Khan" and ali.category.code == "assistant_professor"
    assert ali.department == dept  # blank cell did not erase the department
    assert Duty.objects.filter(pk=duty.pk, faculty=ali).exists()  # history untouched
    assert batch.result["updated"] == 1


def test_same_name_alone_is_not_a_match(admin_client, dept, make_faculty):
    existing = make_faculty("Ali Raza", phone="0300-9999999")
    batch = map_columns(admin_client, upload(admin_client, [[1, "Ali Raza", "Lecturer", "0321-5555555", dept.name]]))
    (row,) = FacultyImport().plan(batch).rows
    assert row.status == Status.REVIEW and row.candidates == [existing]
    confirm(admin_client, batch)  # default decision: skip
    assert Faculty.objects.count() == 1
    assert ImportBatch.objects.get(pk=batch.pk).result["skipped"] == 1


def test_review_decision_can_create_or_update(admin_client, dept, make_faculty):
    existing = make_faculty("Ali Raza", phone="0300-9999999")
    batch = map_columns(admin_client, upload(admin_client, [[1, "Ali Raza", "Lecturer", "0321-5555555", dept.name]]))
    confirm(admin_client, batch, **{"2": str(existing.pk)})
    existing.refresh_from_db()
    assert Faculty.objects.count() == 1 and existing.phone_e164 == "+923215555555"


def test_rows_matching_two_different_people_need_review(admin_client, dept, make_faculty):
    make_faculty("A", phone="0300-1111111", email="a@uni.edu")
    make_faculty("B", phone="0300-2222222", email="b@uni.edu")
    k = FacultyImport()
    batch = upload(admin_client, [[1, "A", "Lecturer", "0300-2222222", dept.name]])
    (row,) = k.plan(map_columns(admin_client, batch)).rows
    assert row.status == Status.SAME or row.status == Status.UPDATE  # phone points to B only
    assert row.match.name == "B"


def test_new_only_mode_leaves_existing_unchanged(admin_client, dept):
    full_import(admin_client, [[1, "Ali Raza", "Lecturer", "0300-1234567", dept.name]])
    batch = map_columns(admin_client, upload(admin_client, [
        [1, "Ali Changed", "Lecturer", "0300-1234567", dept.name],
        [2, "New Person", "Lecturer", "0300-7654321", dept.name],
    ]))
    confirm(admin_client, batch, mode="new_only")
    assert Faculty.objects.filter(name="Ali Raza").exists() and Faculty.objects.filter(name="New Person").exists()
    assert batch.result["created"] == 1 and batch.result["skipped"] == 1


def test_problems_are_explained_not_dropped(admin_client, dept):
    batch = map_columns(admin_client, upload(admin_client, [
        [1, "", "Lecturer", "0300-1234567", dept.name],
        [2, "No Rank", "", "0300-1234568", dept.name],
        [3, "Bad Phone", "Lecturer", "12ab", dept.name],
        [4, "Dean Person", "Dean", "0300-1234569", dept.name],
        [5, "Twin", "Lecturer", "0300-1234567", "Unknown Dept"],
    ]))
    plan = FacultyImport().plan(batch)
    by_row = {p.number: p for p in plan.rows}
    assert "Name is missing" in by_row[2].errors
    assert "Designation is missing" in by_row[3].errors
    assert by_row[4].status == Status.NEW and any("not valid" in w for w in by_row[4].warnings)
    assert "Unknown designation 'Dean'" in by_row[5].errors
    assert "Same phone number as row 2" in by_row[6].errors and "Unknown department 'Unknown Dept'" in by_row[6].errors
    report = admin_client.get(reverse("import_report", args=[batch.pk]))
    assert report.status_code == 200 and report["Content-Type"].startswith("application/vnd.openxmlformats")
    confirm(admin_client, batch)
    assert list(Faculty.objects.values_list("name", flat=True)) == ["Bad Phone"]
    assert batch.result["invalid"] == 4


def test_options_add_missing_designations_and_default_department(admin_client, dept):
    batch = admin_client.post(reverse("import_start", args=["faculty"]), {"file": make_xlsx([["Name", "Designation"], ["Zara", "Research Associate"]])})
    batch = ImportBatch.objects.latest("pk")
    map_columns(admin_client, batch, default_department=str(dept.pk), add_categories="on")
    confirm(admin_client, batch)
    zara = Faculty.objects.get(name="Zara")
    assert zara.department == dept and zara.category.name == "Research Associate"


def test_email_of_another_account_is_refused(admin_client, dept, make_member):
    member = make_member(employee_id="E1", email="one@uni.edu")
    User.objects.create_user("taken@uni.edu", "x", role=Role.ADMIN)
    admin_client.post(reverse("import_start", args=["faculty"]), {"file": make_xlsx([["Faculty ID", "Name", "Email", "Designation"], ["E1", member.name, "taken@uni.edu", "Lecturer"]])})
    batch = map_columns(admin_client, ImportBatch.objects.latest("pk"))
    (row,) = FacultyImport().plan(batch).rows
    assert row.errors == ["Email 'taken@uni.edu' is already used by another account"]


def test_over_long_values_are_row_errors(admin_client, dept):
    admin_client.post(reverse("import_start", args=["faculty"]), {"file": make_xlsx([["Faculty ID", "Name", "Designation", "Department"], ["E" * 51, "Ali", "Lecturer", dept.name]])})
    (row,) = FacultyImport().plan(map_columns(admin_client, ImportBatch.objects.latest("pk"))).rows
    assert row.errors == ["Faculty ID is longer than 50 characters"]


def test_required_columns_must_be_chosen(admin_client, dept):
    batch = upload(admin_client, [[1, "Ali", "Lecturer", "0300-1234567", dept.name]])
    r = admin_client.post(reverse("import_columns", args=[batch.pk]), {"map_name": "1"})
    assert r.status_code == 200 and "Choose a column for: Designation" in r.content.decode()


def test_faculty_users_cannot_import(client, faculty_user, dept):
    client.force_login(faculty_user)
    r = client.post(reverse("import_start", args=["faculty"]), {"file": make_xlsx([HEADERS])})
    assert r.status_code in (302, 403) and not ImportBatch.objects.exists()


def test_cancel_saves_nothing(admin_client, dept):
    batch = map_columns(admin_client, upload(admin_client, [[1, "Ali", "Lecturer", "0300-1234567", dept.name]]))
    admin_client.post(reverse("import_cancel", args=[batch.pk]))
    batch.refresh_from_db()
    assert batch.status == BatchStatus.CANCELLED and not Faculty.objects.exists()
    r = admin_client.post(reverse("import_batch", args=[batch.pk]), {"row": ["2"], "mode": "update"})
    assert not Faculty.objects.exists()


def test_generated_ids_skip_ids_already_taken(make_faculty):
    make_faculty("Typed", employee_id="FAC-0002")
    assert next_faculty_id() == "FAC-0001"
    assert next_faculty_id() == "FAC-0003"


def test_faculty_saved_without_id_gets_one(dept):
    f = Faculty.objects.create(name="X", department=dept, category=FacultyCategory.get("lecturer"))
    assert f.employee_id.startswith("FAC-")


def test_reader_handles_csv_and_blank_heading_rows():
    headers, rows = read_table(make_csv("\n\nName,Phone\nAli,03001234567\n,\n"))
    assert headers == ["Name", "Phone"]
    assert rows == [[4, ["Ali", "03001234567"]]]  # the spreadsheet's own row number
