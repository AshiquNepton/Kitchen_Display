from django.urls import path
from . import views

urlpatterns = [
    path("", views.board, name="board"),
    path("setup/", views.setup, name="setup"),
    path("api/unlock/", views.unlock),
    path("api/categories/", views.list_categories),
    path("api/station-categories/", views.save_station_categories),
    path("api/orders/", views.orders),
    path("api/orders/<int:inv>/items/", views.items),
    path("api/orders/<int:inv>/items/<int:slno>/", views.toggle),
    path("api/orders/<int:inv>/item/", views.toggle_body),
    path("api/orders/<int:inv>/complete/", views.complete),
    path("api/orders/<int:inv>/print/", views.print_receipt),
    path("api/printers/", views.list_printers),
    path("api/close/", views.close_app),
]
