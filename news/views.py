import io
import json
import math
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.mixins import UserPassesTestMixin
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.decorators import method_decorator
from django.views import View
from django.views.generic import TemplateView
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Attendance, Guruh, Student

THRESHOLD = 0.5          # kichikroq = qattiqroq tanish (face-api uchun odatiy: 0.6)
MAX_FAILS = 5            # xato urinishlar soni
LOCK_SECONDS = 300       # bloklash muddati (5 daqiqa)
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB


# ---------- yordamchi funksiyalar ----------

def _client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _valid_descriptor(desc):
    return (
        isinstance(desc, list)
        and len(desc) == 128
        and all(isinstance(x, (int, float)) for x in desc)
    )


# ---------- ruxsatlar ----------

def is_teacher(user):
    """Ustoz profili bor va faol foydalanuvchi."""
    return (
        user.is_authenticated
        and hasattr(user, "teacher_profile")
        and user.teacher_profile.is_active
    )


class StaffOrTeacherMixin(UserPassesTestMixin):
    """Ustoz (faqat o'z guruhi) yoki admin/staff (hamma o'quvchi)."""
    login_url = "login"

    def test_func(self):
        user = self.request.user
        return is_teacher(user) or (user.is_authenticated and user.is_staff)

    def get_students(self):
        user = self.request.user
        qs = Student.objects.filter(is_active=True)
        if is_teacher(user):
            return qs.filter(group__teacher=user.teacher_profile)
        return qs


class TeacherOnlyMixin(StaffOrTeacherMixin):
    """Faqat ustozlar. Admin kirsa, o'tganlar sahifasiga yo'naltiriladi."""

    def test_func(self):
        return is_teacher(self.request.user)

    def handle_no_permission(self):
        user = self.request.user
        if user.is_authenticated and user.is_staff:
            return redirect("present_list")
        return super().handle_no_permission()


staff_required = method_decorator(staff_member_required, name="dispatch")


class DayMixin:
    def get_one_day(self):
        """?date=YYYY-MM-DD bo'lsa shu kun, bo'lmasa None."""
        return parse_date(self.request.GET.get("date", ""))

    def get_day(self):
        """?date bo'lsa shu kun, bo'lmasa bugun."""
        return self.get_one_day() or timezone.localdate()


# ---------- kiosk (talabalar uchun, login kerak emas) ----------

class KioskView(TemplateView):
    template_name = "davomat/kiosk.html"


class CheckinView(View):
    http_method_names = ["post"]

    def mark(self, student):
        obj, created = Attendance.objects.get_or_create(
            student=student, date=timezone.localdate()
        )
        return JsonResponse({
            "ok": True,
            "name": student.full_name,
            "already": not created,
            "time": timezone.localtime(obj.checked_at).strftime("%H:%M"),
        })

    def check_pin(self, request, pin):
        key = f"pin_fails:{_client_ip(request)}"
        if cache.get(key, 0) >= MAX_FAILS:
            return JsonResponse(
                {"ok": False, "error": "Juda ko'p xato urinish. Birozdan keyin urinib ko'ring."},
                status=429,
            )
        student = Student.objects.filter(pin=pin, is_active=True).first()
        if not student:
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, LOCK_SECONDS)
            return JsonResponse({"ok": False, "error": "PIN topilmadi"})
        cache.delete(key)
        return self.mark(student)

    def check_face(self, desc):
        if not _valid_descriptor(desc):
            return JsonResponse({"ok": False, "error": "Yuz aniqlanmadi"})

        best, best_d = None, 9.0
        for s in Student.objects.filter(is_active=True).exclude(face__isnull=True):
            if not s.face or len(s.face) != 128:
                continue
            d = _dist(desc, s.face)
            if d < best_d:
                best, best_d = s, d

        if best and best_d < THRESHOLD:
            return self.mark(best)
        return JsonResponse({"ok": False, "error": "Tanilmadi"})

    def post(self, request, *args, **kwargs):
        try:
            data = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"ok": False, "error": "Noto'g'ri so'rov"}, status=400)

        pin = str(data.get("pin") or "").strip()
        if pin:
            return self.check_pin(request, pin)
        return self.check_face(data.get("descriptor"))


# ---------- yuz kiritish (faqat admin/staff) ----------

@staff_required
class EnrollPageView(TemplateView):
    template_name = "davomat/enroll.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["student"] = get_object_or_404(Student, pk=self.kwargs["student_id"])
        return context


@staff_required
class SaveFaceView(View):
    http_method_names = ["post"]

    def post(self, request, student_id):
        student = get_object_or_404(Student, pk=student_id)

        try:
            descriptor = json.loads(request.POST.get("descriptor", ""))
        except json.JSONDecodeError:
            descriptor = None

        if not _valid_descriptor(descriptor):
            return JsonResponse({"ok": False, "error": "Yuz ma'lumoti noto'g'ri"}, status=400)

        photo = request.FILES.get("photo")
        if photo:
            if photo.size > MAX_PHOTO_SIZE:
                return JsonResponse({"ok": False, "error": "Rasm 5 MB dan katta"}, status=400)
            if not photo.content_type.startswith("image/"):
                return JsonResponse({"ok": False, "error": "Faqat rasm yuklang"}, status=400)
            if student.photo:
                student.photo.delete(save=False)
            student.photo.save(f"student_{student.id}.jpg", photo, save=False)

        student.face = descriptor
        student.save()
        return JsonResponse({"ok": True})


# ---------- ustoz profili: har kun alohida ----------

class TeacherProfileView(TeacherOnlyMixin, DayMixin, TemplateView):
    template_name = "davomat/profile.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.request.user.teacher_profile
        groups = teacher.groups.all()
        today = timezone.localdate()

        students_qs = self.get_students()
        group_id = self.request.GET.get("group", "")
        if group_id.isdigit():
            students_qs = students_qs.filter(group_id=group_id)
        students = list(students_qs.select_related("group").order_by("full_name"))

        one_day = self.get_one_day()
        try:
            limit = min(max(int(self.request.GET.get("limit", 14)), 1), 120)
        except ValueError:
            limit = 14

        if one_day:
            days = [one_day]
        else:
            # davomat yozilgan kunlar (dars kunlari) + bugun
            lesson_days = (
                Attendance.objects.filter(student__in=students_qs)
                .values_list("date", flat=True)
                .distinct()
                .order_by("-date")[:limit]
            )
            days = sorted(set(lesson_days) | {today}, reverse=True)[:limit]

        records = (
            Attendance.objects.filter(date__in=days, student__in=students_qs)
            .select_related("student", "student__group")
            .order_by("checked_at")
        )
        by_day = defaultdict(list)
        for a in records:
            by_day[a.date].append(a)

        blocks = []
        for d in days:
            came = by_day.get(d, [])
            came_ids = {a.student_id for a in came}
            absent = [s for s in students if s.id not in came_ids]
            blocks.append({"date": d, "came": came, "absent": absent})

        context.update({
            "teacher": teacher,
            "groups": groups,
            "group_id": group_id,
            "blocks": blocks,
            "one_day": one_day,
            "next_limit": limit + 14,
            "has_today": today in days,
            "has_more": not one_day and len(days) >= limit,
        })
        return context


# ---------- ustoz o'z guruhlarini boshqaradi ----------

class GroupsView(TeacherOnlyMixin, TemplateView):
    template_name = "davomat/groups.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        teacher = self.request.user.teacher_profile
        context["groups"] = teacher.groups.prefetch_related("students")
        # hech qaysi guruhga kirmagan talabalar
        context["free_students"] = Student.objects.filter(
            is_active=True, group__isnull=True
        ).order_by("full_name")
        return context


class GroupCreateView(TeacherOnlyMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        teacher = request.user.teacher_profile
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Guruh nomini yozing.")
        elif teacher.groups.filter(name__iexact=name).exists():
            messages.error(request, "Bunday guruh sizda allaqachon bor.")
        else:
            Guruh.objects.create(name=name, teacher=teacher)
            messages.success(request, f"«{name}» guruhi yaratildi.")
        return redirect("groups")


class StudentAssignView(TeacherOnlyMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        teacher = request.user.teacher_profile
        student_id = request.POST.get("student_id", "")
        group_id = request.POST.get("group_id", "")

        if not student_id.isdigit():
            return redirect("groups")
        student = get_object_or_404(Student, pk=student_id, is_active=True)

        # faqat guruhsiz yoki o'z guruhidagi talabani o'tkazish mumkin
        if student.group_id and student.group.teacher_id != teacher.id:
            messages.error(request, "Bu talaba boshqa ustozning guruhida.")
            return redirect("groups")

        if group_id == "":
            student.group = None
        elif group_id.isdigit():
            student.group = get_object_or_404(Guruh, pk=group_id, teacher=teacher)
        else:
            return redirect("groups")

        student.save(update_fields=["group"])
        messages.success(request, f"{student.full_name} saqlandi.")
        return redirect("groups")


# ---------- bitta kun: o'tganlar / o'tmaganlar / tablo ----------

class PresentListView(StaffOrTeacherMixin, DayMixin, TemplateView):
    template_name = "davomat/present.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        day = self.get_day()
        context["day"] = day
        context["came"] = (
            Attendance.objects.filter(date=day, student__in=self.get_students())
            .select_related("student")
            .order_by("checked_at")
        )
        return context


class AbsentListView(StaffOrTeacherMixin, DayMixin, TemplateView):
    template_name = "davomat/absent.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        day = self.get_day()
        students = self.get_students()
        came_ids = Attendance.objects.filter(date=day).values_list("student_id", flat=True)
        context["day"] = day
        context["absent"] = students.exclude(id__in=came_ids).select_related("group")
        return context


class BoardView(StaffOrTeacherMixin, DayMixin, TemplateView):
    template_name = "davomat/board.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        day = self.get_day()
        students = self.get_students()
        came = (
            Attendance.objects.filter(date=day, student__in=students)
            .select_related("student")
            .order_by("-checked_at")
        )
        came_ids = came.values_list("student_id", flat=True)
        absent = students.exclude(id__in=came_ids)

        context.update({
            "day": day,
            "is_today": day == timezone.localdate(),
            "came": came,
            "absent": absent,
            "came_count": came.count(),
            "absent_count": absent.count(),
        })
        return context


class ReportView(StaffOrTeacherMixin, View):
    # eski manzil buzilmasligi uchun yo'naltiradi
    def get(self, request, *args, **kwargs):
        if is_teacher(request.user):
            return redirect("profile")
        return redirect("present_list")


# ---------- haftalik PDF ----------

WEEKDAYS = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]


def _pdf_fonts():
    regular = Path(settings.BASE_DIR) / "fonts" / "Font-Regular.ttf"
    bold = Path(settings.BASE_DIR) / "fonts" / "Font-Bold.ttf"
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont("AppFont", str(regular)))
        pdfmetrics.registerFont(TTFont("AppFont-Bold", str(bold)))
        return "AppFont", "AppFont-Bold"
    return "Helvetica", "Helvetica-Bold"


class WeeklyPdfView(StaffOrTeacherMixin, DayMixin, View):
    """?date=YYYY-MM-DD (shu sana kirgan hafta), ?week=prev (o'tgan hafta), ?group=<id>"""

    def get(self, request, *args, **kwargs):
        day = self.get_day()
        if request.GET.get("week") == "prev":
            day = day - timedelta(days=7)
        monday = day - timedelta(days=day.weekday())
        week = [monday + timedelta(days=i) for i in range(7)]

        students_qs = self.get_students()
        group_id = request.GET.get("group", "")
        group_name = "Barcha guruhlar"
        if group_id.isdigit():
            students_qs = students_qs.filter(group_id=group_id)
            first = students_qs.select_related("group").first()
            if first and first.group:
                group_name = first.group.name
        students = list(
            students_qs.select_related("group").order_by("group__name", "full_name")
        )

        records = Attendance.objects.filter(date__in=week, student__in=students_qs)
        came = {
            (a.student_id, a.date): timezone.localtime(a.checked_at).strftime("%H:%M")
            for a in records
        }

        font, font_bold = _pdf_fonts()
        small = ParagraphStyle("small", fontName=font, fontSize=8, leading=10)
        title_style = ParagraphStyle("title", fontName=font_bold, fontSize=14, leading=18)
        info_style = ParagraphStyle("info", fontName=font, fontSize=10, leading=14)

        header = ["T/r", "F.I.SH.", "Guruh"] + [
            f"{WEEKDAYS[i]}\n{d:%d.%m}" for i, d in enumerate(week)
        ] + ["Jami"]
        rows = [header]
        styles = [
            ("FONTNAME", (0, 0), (-1, -1), font),
            ("FONTNAME", (0, 0), (-1, 0), font_bold),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
            ("ALIGN", (3, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]

        day_totals = [0] * 7
        for n, s in enumerate(students, start=1):
            row = [
                str(n),
                Paragraph(s.full_name, small),
                Paragraph(s.group.name if s.group else "-", small),
            ]
            count = 0
            for i, d in enumerate(week):
                t = came.get((s.id, d))
                if t:
                    row.append(t)
                    count += 1
                    day_totals[i] += 1
                    styles.append(("BACKGROUND", (3 + i, n), (3 + i, n), colors.HexColor("#d9f2e0")))
                else:
                    row.append("-")
                    styles.append(("TEXTCOLOR", (3 + i, n), (3 + i, n), colors.HexColor("#b42318")))
            row.append(f"{count}/7")
            rows.append(row)

        rows.append(["", "Kunlik jami o'tganlar", ""] + [str(x) for x in day_totals] + [""])
        last = len(rows) - 1
        styles += [
            ("FONTNAME", (0, last), (-1, last), font_bold),
            ("BACKGROUND", (0, last), (-1, last), colors.HexColor("#f2f2f2")),
        ]

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=landscape(A4),
            leftMargin=12 * mm, rightMargin=12 * mm,
            topMargin=12 * mm, bottomMargin=12 * mm,
            title=f"Haftalik davomat {monday:%d.%m.%Y}",
        )
        table = Table(
            rows, repeatRows=1,
            colWidths=[10 * mm, 62 * mm, 32 * mm] + [20 * mm] * 7 + [16 * mm],
        )
        table.setStyle(TableStyle(styles))

        doc.build([
            Paragraph("Haftalik davomat", title_style),
            Paragraph(
                f"Hafta: {week[0]:%d.%m.%Y} - {week[-1]:%d.%m.%Y} &nbsp;|&nbsp; "
                f"Guruh: {group_name} &nbsp;|&nbsp; O'quvchilar: {len(students)}",
                info_style,
            ),
            Spacer(1, 6 * mm),
            table,
        ])

        response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="haftalik_davomat_{monday:%Y-%m-%d}.pdf"'
        )
        return response