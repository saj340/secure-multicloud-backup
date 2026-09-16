from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render

from .forms import LoginForm, RegistrationForm


def register_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Account created. You can now log in.")
            return redirect("accounts:login")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:home")

    if request.method == "POST":
        form = LoginForm(request.POST)
        if form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["email"],
                password=form.cleaned_data["password"],
            )
            if user is not None:
                login(request, user)
                return redirect("accounts:home")
            form.add_error(None, "Invalid email or password.")
    else:
        form = LoginForm()

    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@login_required
def home_view(request):
    return render(request, "accounts/home.html")
