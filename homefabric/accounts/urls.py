from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from homefabric.accounts import views

app_name = "accounts"
urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("account/", views.profile, name="profile"),
    path("account/sign-out-everywhere/", views.sign_out_everywhere, name="sign_out_everywhere"),
    path("account/password/", auth_views.PasswordChangeView.as_view(
        template_name="accounts/password_change.html",
        success_url=reverse_lazy("accounts:password_change_done")), name="password_change"),
    path("account/password/done/", auth_views.PasswordChangeDoneView.as_view(
        template_name="accounts/password_change_done.html"), name="password_change_done"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html",
        success_url=reverse_lazy("accounts:password_reset_complete")),
        name="password_reset_confirm"),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
    path("invite/<str:token>/", views.accept_invitation, name="accept_invitation"),
    path("household/switch/", views.switch_household, name="switch_household"),
    path("household/none/", views.no_household, name="no_household"),
    path("household/settings/", views.household_settings, name="household_settings"),
    path("household/members/", views.members, name="members"),
    path("household/members/<int:pk>/role/", views.member_role, name="member_role"),
    path("household/members/<int:pk>/deactivate/", views.member_deactivate,
         name="member_deactivate"),
    path("household/members/<int:pk>/reactivate/", views.member_reactivate,
         name="member_reactivate"),
    path("household/invitations/<uuid:pk>/revoke/", views.invitation_revoke,
         name="invitation_revoke"),
    path("households/new/", views.household_create, name="household_create"),
    path("instance/users/", views.user_list, name="user_list"),
    path("instance/users/<int:pk>/toggle/", views.user_toggle, name="user_toggle"),
    path("instance/users/<int:pk>/setup-link/", views.user_setup_link, name="user_setup_link"),
]
