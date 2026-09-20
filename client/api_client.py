import requests

from . import config

DEFAULT_TIMEOUT = 10
TRANSFER_TIMEOUT = 300


class ApiError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ApiClient:
    def __init__(self, base_url=None, ca_cert=None):
        self.base_url = base_url or config.api_url()
        config.require_safe_transport(self.base_url)
        # The server's certificate is always verified: against our own CA when one
        # is configured (a development server), otherwise the system's trusted CAs.
        self._verify = ca_cert or config.ca_cert_for(self.base_url) or True
        self.token = None

    def _headers(self):
        if self.token:
            return {"Authorization": f"Token {self.token}"}
        return {}

    def _request(self, method, path, expected=(200,), timeout=DEFAULT_TIMEOUT, **kwargs):
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        try:
            response = requests.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                timeout=timeout,
                verify=self._verify,
                **kwargs,
            )
        except requests.exceptions.SSLError as exc:
            # The handshake failed before any request was sent, so nothing
            # (including the password) reached the unverified server.
            raise ApiError(
                "The server's security certificate could not be verified, so the connection "
                "was refused. Nothing was sent."
            ) from exc
        except requests.RequestException as exc:
            raise ApiError("Could not reach the server. Check that it is running.") from exc
        if response.status_code not in expected:
            raise ApiError(self._extract_error(response), response.status_code)
        return response

    # ---------------------------------------------------------- accounts

    def register(self, email, full_name, password1, password2):
        return self._request(
            "POST",
            "/accounts/register/",
            expected=(201,),
            json={
                "email": email,
                "full_name": full_name,
                "password1": password1,
                "password2": password2,
            },
        ).json()

    def login(self, email, password):
        data = self._request(
            "POST", "/accounts/login/", json={"email": email, "password": password}
        ).json()
        self.token = data["token"]
        return data["user"]

    def logout(self):
        try:
            self._request("POST", "/accounts/logout/", expected=(204,))
        finally:
            self.token = None

    def me(self):
        return self._request("GET", "/accounts/me/").json()

    def request_password_reset(self, email):
        return self._request(
            "POST", "/accounts/password-reset/request/", json={"email": email}
        ).json()

    def confirm_password_reset(self, code, new_password1, new_password2):
        return self._request(
            "POST",
            "/accounts/password-reset/confirm/",
            json={
                "code": code,
                "new_password1": new_password1,
                "new_password2": new_password2,
            },
        ).json()

    # --------------------------------------------------------- providers

    def list_provider_types(self):
        return self._request("GET", "/providers/types/").json()

    def list_providers(self):
        return self._request("GET", "/providers/").json()

    def connect_provider(self, provider_type, display_name, credentials):
        return self._request(
            "POST",
            "/providers/",
            expected=(201,),
            json={
                "provider_type": provider_type,
                "display_name": display_name,
                "credentials": credentials,
            },
        ).json()

    def check_provider(self, provider_id):
        return self._request("POST", f"/providers/{provider_id}/check/").json()

    def disconnect_provider(self, provider_id):
        return self._request("POST", f"/providers/{provider_id}/disconnect/").json()

    # ----------------------------------------------------------- backups

    def list_jobs(self):
        return self._request("GET", "/backups/jobs/").json()

    def get_job(self, job_id):
        return self._request("GET", f"/backups/jobs/{job_id}/").json()

    def create_job(self, name, paths):
        return self._request(
            "POST", "/backups/jobs/", expected=(201,), json={"name": name, "paths": paths}
        ).json()

    def begin_job(self, job_id):
        return self._request("POST", f"/backups/jobs/{job_id}/begin/").json()

    def finish_job(self, job_id):
        return self._request("POST", f"/backups/jobs/{job_id}/finish/").json()

    def start_version(self, file_id, original_filename, original_size):
        """Returns (version, data_key). The key encrypts the file locally."""
        data = self._request(
            "POST",
            f"/backups/files/{file_id}/versions/",
            expected=(201,),
            json={"original_filename": original_filename, "original_size": original_size},
        ).json()
        return data["version"], data["data_key"]

    def upload_ciphertext(self, version_id, ciphertext):
        return self._request(
            "PUT",
            f"/backups/versions/{version_id}/ciphertext/",
            timeout=TRANSFER_TIMEOUT,
            data=ciphertext,
            headers={"Content-Type": "application/octet-stream"},
        ).json()

    # -------------------------------------------------------- recoveries

    def list_recoveries(self):
        return self._request("GET", "/backups/recoveries/").json()

    def start_recovery(self, version_id):
        return self._request(
            "POST", "/backups/recoveries/", expected=(201,), json={"version_id": version_id}
        ).json()

    def download_recovery_ciphertext(self, operation_id):
        return self._request(
            "GET",
            f"/backups/recoveries/{operation_id}/ciphertext/",
            timeout=TRANSFER_TIMEOUT,
        ).content

    def get_recovery_key(self, operation_id):
        return self._request("GET", f"/backups/recoveries/{operation_id}/key/").json()["data_key"]

    def complete_recovery(self, operation_id):
        return self._request("POST", f"/backups/recoveries/{operation_id}/complete/").json()

    def fail_recovery(self, operation_id, message):
        return self._request(
            "POST", f"/backups/recoveries/{operation_id}/fail/", json={"message": message}
        ).json()

    @staticmethod
    def _extract_error(response):
        try:
            data = response.json()
        except ValueError:
            return f"Server error ({response.status_code})"

        if not isinstance(data, dict):
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
