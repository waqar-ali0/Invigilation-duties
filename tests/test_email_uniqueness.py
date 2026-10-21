"""Emails are unique ignoring letter case, for accounts and for faculty."""

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from accounts.models import User
from faculty.models import Faculty

ACCOUNTS = [("accounts", "0001_initial")]
FACULTY = [("faculty", "0002_faculty_invited_at")]


@pytest.mark.django_db
def test_user_email_unique_ignoring_case():
    User.objects.create_user("Ali@uni.edu", "pw12345!")
    with pytest.raises(ValidationError, match="already exists"):
        User(email="ali@uni.edu").validate_constraints()
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.create_user("ali@uni.edu", "pw12345!")


@pytest.mark.django_db
def test_faculty_email_unique_ignoring_case(make_faculty):
    make_faculty(email="Ali@uni.edu")
    with pytest.raises(ValidationError, match="already exists"):
        Faculty(email="ali@uni.edu").validate_constraints()
    with pytest.raises(IntegrityError), transaction.atomic():
        make_faculty(email="ali@UNI.edu")


def migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "before,make,message",
    [
        (
            ACCOUNTS,
            lambda apps: [apps.get_model("accounts", "User").objects.create(email=e) for e in ("Ali@uni.edu", "ali@uni.edu")],
            "Ali@uni.edu, ali@uni.edu",
        ),
        (
            FACULTY,
            lambda apps: [
                apps.get_model("faculty", "Faculty").objects.create(
                    employee_id=e, name=e, email=e + "@uni.edu", designation="lecturer",
                    department=apps.get_model("faculty", "Department").objects.get_or_create(name="CS")[0],
                )
                for e in ("Sara", "SARA")
            ],
            "SARA@uni.edu, Sara@uni.edu",
        ),
    ],
)
def test_migration_refuses_case_duplicates(before, make, message):
    latest = MigrationExecutor(connection).loader.graph.leaf_nodes()
    apps = migrate(before)
    try:
        rows = make(apps)
        with pytest.raises(RuntimeError, match=f"(?s)differ only in letter case.*{message}"):
            migrate(latest)
        for row in rows:
            row.delete()
    finally:
        migrate(latest)
