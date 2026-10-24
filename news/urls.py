from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [
    path("", views.KioskView.as_view(), name="kiosk"),
    path("checkin/", views.CheckinView.as_view(), name="checkin"),
    path("enroll/<int:student_id>/", views.EnrollPageView.as_view(), name="enroll_page"),
    path("enroll/<int:student_id>/save/", views.SaveFaceView.as_view(), name="save_face"),
    path("kirish/", auth_views.LoginView.as_view(template_name="davomat/login.html"), name="login"),
    path("chiqish/", auth_views.LogoutView.as_view(), name="logout"),
    path("profil/", views.TeacherProfileView.as_view(), name="profile"),
    path("tablo/", views.BoardView.as_view(), name="board"),
    path("present/", views.PresentListView.as_view(), name="present_list"),
    path("absent/", views.AbsentListView.as_view(), name="absent_list"),
    path("pdf/", views.WeeklyPdfView.as_view(), name="weekly_pdf"),
]