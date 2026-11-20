from django.urls import path

from .views import AdminMediaDetailView, AdminMediaListCreateView, AdminMediaReplaceView

urlpatterns = [
    path("admin/media/", AdminMediaListCreateView.as_view(), name="admin-media"),
    path("admin/media/<int:pk>/", AdminMediaDetailView.as_view(), name="admin-media-detail"),
    path("admin/media/<int:pk>/replace/", AdminMediaReplaceView.as_view(), name="admin-media-replace"),
]
