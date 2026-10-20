from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.text import slugify

from accounts.models import Role
from accounts.permissions import role_required
from exams.models import ExamPeriod

from .excel import load_xlsx, roster_xlsx
from .pdf import roster_pdf

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(period, build, suffix, content_type):
    response = HttpResponse(build(period), content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{slugify(period.name)}-{suffix}"'
    return response


@role_required(Role.ADMIN)
def roster_pdf_view(request, pk):
    return _download(get_object_or_404(ExamPeriod, pk=pk), roster_pdf, "roster.pdf", "application/pdf")


@role_required(Role.ADMIN)
def roster_xlsx_view(request, pk):
    return _download(get_object_or_404(ExamPeriod, pk=pk), roster_xlsx, "roster.xlsx", XLSX)


@role_required(Role.ADMIN)
def load_xlsx_view(request, pk):
    return _download(get_object_or_404(ExamPeriod, pk=pk), load_xlsx, "load.xlsx", XLSX)
