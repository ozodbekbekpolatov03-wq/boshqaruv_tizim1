from django.conf import settings
from django.db import models
from django.utils import timezone


class Teacher(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="teacher_profile",
        verbose_name="Foydalanuvchi (login)",
    )
    full_name = models.CharField("F.I.SH.", max_length=200)
    phone = models.CharField("Telefon", max_length=20, blank=True)
    photo = models.ImageField("Rasm", upload_to="teachers/", null=True, blank=True)
    is_active = models.BooleanField("Faol", default=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "Ustoz"
        verbose_name_plural = "Ustozlar"

    def __str__(self):
        return self.full_name


class Guruh(models.Model):
    name = models.CharField("Guruh nomi", max_length=100)
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="groups",
        verbose_name="Ustoz",
    )

    class Meta:
        ordering = ["name"]
        verbose_name = "Guruh"
        verbose_name_plural = "Guruhlar"

    def __str__(self):
        return self.name


class Student(models.Model):
    full_name = models.CharField("F.I.SH.", max_length=200)
    group = models.ForeignKey(
        Guruh,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
        verbose_name="Guruh",
    )
    pin = models.CharField("PIN (zaxira)", max_length=10, unique=True)
    photo = models.ImageField("Rasm", upload_to="students/", null=True, blank=True)
    face = models.JSONField("Yuz ma'lumoti", null=True, blank=True)
    is_active = models.BooleanField("Faol", default=True)

    class Meta:
        ordering = ["full_name"]
        verbose_name = "O'quvchi"
        verbose_name_plural = "O'quvchilar"

    def __str__(self):
        return self.full_name

    @property
    def has_face(self):
        return bool(self.face)


class Attendance(models.Model):
    student = models.ForeignKey(
        Student,
        on_delete=models.CASCADE,
        related_name="attendances",
        verbose_name="O'quvchi",
    )
    date = models.DateField("Sana", default=timezone.localdate)
    checked_at = models.DateTimeField("Kelgan vaqt", auto_now_add=True)

    class Meta:
        ordering = ["-checked_at"]
        verbose_name = "Davomat"
        verbose_name_plural = "Davomatlar"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "date"], name="unique_student_date"
            )
        ]

    def __str__(self):
        return f"{self.student} — {self.date:%d.%m.%Y}"


# ---------- Admin'da alohida ro'yxatlar uchun proxy modellar ----------

class PresentStudent(Student):
    class Meta:
        proxy = True
        verbose_name = "Davomatdan o'tgan o'quvchi"
        verbose_name_plural = "Davomatdan o'tganlar"


class AbsentStudent(Student):
    class Meta:
        proxy = True
        verbose_name = "Davomatdan o'tmagan o'quvchi"
        verbose_name_plural = "Davomatdan o'tmaganlar"
