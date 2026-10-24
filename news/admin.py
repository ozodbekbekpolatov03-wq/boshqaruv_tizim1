from django.contrib import admin
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    AbsentStudent,
    Attendance,
    Guruh,
    PresentStudent,
    Student,
    Teacher,
)


def _today_ids():
    return Attendance.objects.filter(
        date=timezone.localdate()
    ).values_list("student_id", flat=True)


class TodayFilter(admin.SimpleListFilter):
    title = "Bugungi davomat"
    parameter_name = "bugun"

    def lookups(self, request, model_admin):
        return [
            ("keldi", "Davomatdan o'tgan"),
            ("kelmadi", "Davomatdan o'tmagan"),
        ]

    def queryset(self, request, qs):
        if self.value() == "keldi":
            return qs.filter(id__in=_today_ids())
        if self.value() == "kelmadi":
            return qs.exclude(id__in=_today_ids())
        return qs


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("full_name", "group", "pin", "rasm", "has_face", "is_active", "yuz_havola")
    list_filter = ("is_active", "group", TodayFilter)
    search_fields = ("full_name", "pin")
    readonly_fields = ("rasm", "yuz_havola")
    fields = ("full_name", "group", "pin", "photo", "rasm", "yuz_havola", "is_active")
    actions = ["yuzni_ochirish"]

    @admin.display(boolean=True, description="Yuz kiritilgan")
    def has_face(self, obj):
        return obj.has_face

    @admin.display(description="Rasm")
    def rasm(self, obj):
        if obj.photo:
            return format_html(
                '<img src="{}" style="height:50px;border-radius:6px">', obj.photo.url
            )
        return "—"

    @admin.display(description="Yuz")
    def yuz_havola(self, obj):
        if not obj.pk:
            return "Avval o'quvchini saqlang"
        url = reverse("enroll_page", args=[obj.pk])
        return format_html('<a href="{}">Yuzni kiritish</a>', url)

    @admin.action(description="Tanlanganlarning yuz ma'lumotini o'chirish")
    def yuzni_ochirish(self, request, queryset):
        updated = queryset.update(face=None)
        self.message_user(request, f"{updated} ta o'quvchining yuz ma'lumoti o'chirildi.")


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("full_name", "user", "phone", "is_active")
    search_fields = ("full_name", "phone", "user__username")
    list_filter = ("is_active",)


@admin.register(Guruh)
class GuruhAdmin(admin.ModelAdmin):
    list_display = ("name", "teacher")
    list_filter = ("teacher",)
    search_fields = ("name",)


# ---------- Alohida ro'yxatlar: bugun o'tganlar / o'tmaganlar ----------

class ReadOnlyStudentList(admin.ModelAdmin):
    list_display = ("full_name", "group", "kelgan_vaqt")
    list_filter = ("group",)
    search_fields = ("full_name",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Bugun kelgan vaqti")
    def kelgan_vaqt(self, obj):
        a = obj.attendances.filter(date=timezone.localdate()).first()
        return timezone.localtime(a.checked_at).strftime("%H:%M") if a else "—"


@admin.register(PresentStudent)
class PresentStudentAdmin(ReadOnlyStudentList):
    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            is_active=True, id__in=_today_ids()
        )


@admin.register(AbsentStudent)
class AbsentStudentAdmin(ReadOnlyStudentList):
    list_display = ("full_name", "group")

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            is_active=True
        ).exclude(id__in=_today_ids())


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("student", "date", "kelgan_vaqt")
    list_filter = ("date",)
    search_fields = ("student__full_name",)
    date_hierarchy = "date"

    @admin.display(description="Kelgan vaqt", ordering="checked_at")
    def kelgan_vaqt(self, obj):
        return timezone.localtime(obj.checked_at).strftime("%H:%M")
