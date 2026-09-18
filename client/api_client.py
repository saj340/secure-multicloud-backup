import requests

API_BASE_URL = "http://127.0.0.1:8000/api"


class ApiError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ApiClient:
    def __init__(self, base_url=API_BASE_URL):
        self.base_url = base_url
        self.token = None

    def _headers(self):
        if self.token:
            return {"Authorization": f"Token {self.token}"}
        return {}

    def register(self, email, full_name, password1, password2):
        response = requests.post(
            f"{self.base_url}/accounts/register/",
            json={
                "email": email,
                "full_name": full_name,
                "password1": password1,
                "password2": password2,
            },
            timeout=10,
        )
        if response.status_code != 201:
            raise ApiError(self._extract_error(response), response.status_code)
        return response.json()

    def login(self, email, password):
        response = requests.post(
            f"{self.base_url}/accounts/login/",
            json={"email": email, "password": password},
            timeout=10,
        )
        if response.status_code != 200:
            raise ApiError(self._extract_error(response), response.status_code)
        data = response.json()
        self.token = data["token"]
        return data["user"]

    def logout(self):
        response = requests.post(
            f"{self.base_url}/accounts/logout/",
            headers=self._headers(),
            timeout=10,
        )
        self.token = None
        if response.status_code != 204:
            raise ApiError(self._extract_error(response), response.status_code)

    def me(self):
        response = requests.get(
            f"{self.base_url}/accounts/me/",
            headers=self._headers(),
            timeout=10,
        )
        if response.status_code != 200:
            raise ApiError(self._extract_error(response), response.status_code)
        return response.json()

    def request_password_reset(self, email):
        response = requests.post(
            f"{self.base_url}/accounts/password-reset/request/",
            json={"email": email},
            timeout=10,
        )
        if response.status_code != 200:
            raise ApiError(self._extract_error(response), response.status_code)
        return response.json()

    def confirm_password_reset(self, code, new_password1, new_password2):
        response = requests.post(
            f"{self.base_url}/accounts/password-reset/confirm/",
            json={
                "code": code,
                "new_password1": new_password1,
                "new_password2": new_password2,
            },
            timeout=10,
        )
        if response.status_code != 200:
            raise ApiError(self._extract_error(response), response.status_code)
        return response.json()

    @staticmethod
    def _extract_error(response):
        try:
            data = response.json()
        except ValueError:
            return f"Server error ({response.status_code})"

        if "detail" in data:
            return data["detail"]

        messages = []
        for field, errors in data.items():
            if isinstance(errors, list):
                messages.append(f"{field}: {errors[0]}")
            else:
                messages.append(f"{field}: {errors}")
        return "; ".join(messages) if messages else f"Server error ({response.status_code})"
