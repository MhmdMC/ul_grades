from __future__ import annotations

from dataclasses import dataclass
from http.cookies import SimpleCookie
from time import perf_counter
from typing import Any

import requests

UL_BASE_URL = "http://www.ulfg.ul.edu.lb"
UL_API_PREFIX = "/api/v0.1"
UL_COOKIE_NAME = ".AspNetCore.Identity.Application"
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
}


@dataclass(slots=True)
class ULAPIResponse:
    endpoint: str
    status_code: int
    response_time: float
    duration: float
    json_data: Any | None
    text: str | None = None


class ULAPIError(RuntimeError):
    def __init__(self, message: str, *, response: ULAPIResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


class ULAPIClient:
    def __init__(self, base_url: str = UL_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def _request(self, endpoint: str, cookie_value: str) -> ULAPIResponse:
        url = f"{self.base_url}{endpoint}"
        started = perf_counter()
        response = requests.get(
            url,
            headers=DEFAULT_HEADERS,
            cookies={UL_COOKIE_NAME: cookie_value},
            timeout=20,
        )
        duration = perf_counter() - started
        json_data: Any | None = None
        text: str | None = None
        try:
            json_data = response.json()
        except Exception:
            text = response.text
        return ULAPIResponse(
            endpoint=endpoint,
            status_code=response.status_code,
            response_time=duration,
            duration=duration,
            json_data=json_data,
            text=text,
        )

    def me_endpoint(self) -> str:
        return f"{UL_API_PREFIX}/me"

    def login_endpoint(self) -> str:
        return "/login"

    def classes_endpoint(self, student_username: str) -> str:
        return f"{UL_API_PREFIX}/students/{student_username}/classes"

    def grades_endpoint(self, student_id: str, class_id: str) -> str:
        return f"{UL_API_PREFIX}/students/{student_id}/classes/{class_id}/grades"

    def get_me(self, cookie_value: str) -> ULAPIResponse:
        return self._request(self.me_endpoint(), cookie_value)

    def login_with_credentials(self, username: str, password: str) -> str:
        url = f"{self.base_url}{self.login_endpoint()}"
        started = perf_counter()
        response = requests.post(
            url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "User-Agent": DEFAULT_HEADERS["User-Agent"],
            },
            json={"username": username, "password": password},
            timeout=20,
            allow_redirects=False,
        )
        _duration = perf_counter() - started
        if not 200 <= response.status_code < 300:
            raise ULAPIError(f"UL login failed with status {response.status_code}")

        cookie_value = response.cookies.get(UL_COOKIE_NAME)
        if not cookie_value:
            set_cookie_header = response.headers.get("Set-Cookie", "")
            if set_cookie_header:
                parsed = SimpleCookie()
                parsed.load(set_cookie_header)
                morsel = parsed.get(UL_COOKIE_NAME)
                if morsel and morsel.value:
                    cookie_value = morsel.value

        if not cookie_value:
            raise ULAPIError("UL login succeeded but no auth cookie was returned.")

        return cookie_value

    def get_student_classes(self, student_username: str, cookie_value: str) -> ULAPIResponse:
        return self._request(self.classes_endpoint(student_username), cookie_value)

    def request_grades(self, student_id: str, class_id: str, cookie_value: str) -> ULAPIResponse:
        return self._request(self.grades_endpoint(student_id, class_id), cookie_value)

    def get_grades(self, student_id: str, class_id: str, cookie_value: str) -> ULAPIResponse:
        return self.request_grades(student_id, class_id, cookie_value)
