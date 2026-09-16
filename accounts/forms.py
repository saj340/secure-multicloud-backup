from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import User


class RegistrationForm(forms.Form):
    email = forms.EmailField()
    full_name = forms.CharField(max_length=150, required=False)
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_password1(self):
        password1 = self.cleaned_data.get("password1")
        validate_password(password1)
        return password1

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Passwords do not match.")
        return cleaned_data

    def save(self):
        return User.objects.create_user(
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
            full_name=self.cleaned_data.get("full_name", ""),
        )


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
