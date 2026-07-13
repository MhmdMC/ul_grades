from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_socketio import SocketIO, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect, FlaskForm
from sqlalchemy import func, or_, text
from sqlalchemy.orm import relationship
from werkzeug.security import check_password_hash, generate_password_hash
from wtforms import BooleanField, PasswordField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length

from ul_api import ULAPIClient, ULAPIResponse


BASE_DIR = Path(__file__).resolve().parent
MATERIALS_FILE = BASE_DIR / "materials.json"
DB_FILE = BASE_DIR / "ul_grades.sqlite3"

UL_COOKIE_NAME = ".AspNetCore.Identity.Application"
UL_BASE_URL = os.environ.get("UL_BASE_URL", "http://www.ulfg.ul.edu.lb")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
PASSING_MARK = 57
ul_api = ULAPIClient(UL_BASE_URL)


app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_FILE}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_DURATION=timedelta(days=30),
)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
csrf = CSRFProtect(app)
socketio = SocketIO(app, async_mode="threading", cors_allowed_origins="*", logger=False, engineio_logger=False)
scheduler = BackgroundScheduler(daemon=True)


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    representative_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    last_json = db.Column(db.Text, nullable=True)
    last_poll = db.Column(db.DateTime, nullable=True)
    last_detected_change = db.Column(db.DateTime, nullable=True)
    last_response_time = db.Column(db.Float, nullable=True)
    paused = db.Column(db.Boolean, default=False, nullable=False)

    users = relationship("User", back_populates="group", foreign_keys="User.group_id")
    representative_user = relationship("User", foreign_keys=[representative_user_id], post_update=True)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    ul_name = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    ul_student_id = db.Column(db.String(64), nullable=True)
    ul_class_id = db.Column(db.String(64), nullable=True)
    ul_cookie = db.Column(db.Text, nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=True)
    last_successful_poll = db.Column(db.DateTime, nullable=True)
    last_request_result = db.Column(db.String(255), nullable=True)
    last_snapshot = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at = db.Column(db.DateTime, nullable=True)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    group = relationship("Group", back_populates="users", foreign_keys=[group_id])


class Material(db.Model):
    __tablename__ = "materials"

    material_id = db.Column(db.String(64), primary_key=True)
    code = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    credits = db.Column(db.Integer, nullable=True)
    image = db.Column(db.String(255), nullable=True)


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(120), primary_key=True)
    value = db.Column(db.Text, nullable=False)


class LogEntry(db.Model):
    __tablename__ = "logs"

    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(20), nullable=False)
    event_type = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.id"), nullable=True)
    endpoint = db.Column(db.String(255), nullable=True)
    http_status = db.Column(db.Integer, nullable=True)
    response_time = db.Column(db.Float, nullable=True)
    polling_duration = db.Column(db.Float, nullable=True)
    exception = db.Column(db.Text, nullable=True)


class ULIdentityBootstrapError(RuntimeError):
    pass


class AuthForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6, max=128)])
    submit = SubmitField("Continue")


class RegisterForm(AuthForm):
    ul_cookie = TextAreaField("UL Cookie", validators=[DataRequired(), Length(min=10)])
    remember_me = BooleanField("Remember me", default=True)
    submit = SubmitField("Create account")


class RegisterWithCredentialsForm(AuthForm):
    ul_username = StringField("UL Username", validators=[DataRequired(), Length(min=3, max=80)])
    ul_password = PasswordField("UL Password", validators=[DataRequired(), Length(min=6, max=128)])
    remember_me = BooleanField("Remember me", default=True)
    submit = SubmitField("Create account")


class CookieForm(FlaskForm):
    ul_cookie = TextAreaField("UL Cookie", validators=[DataRequired(), Length(min=10)])
    submit = SubmitField("Save cookie")

class ULCredentialsForm(FlaskForm):
    ul_username = StringField("UL Username", validators=[DataRequired(), Length(min=3, max=80)])
    ul_password = PasswordField("UL Password", validators=[DataRequired(), Length(min=6, max=128)])
    submit = SubmitField("Get cookie")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember me", default=True)
    submit = SubmitField("Login")


class AdminCreateUserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6, max=128)])
    ul_cookie = TextAreaField("UL Cookie", validators=[DataRequired()])
    submit = SubmitField("Create")


class AdminResetClassCacheForm(FlaskForm):
    submit = SubmitField("Reset cached class IDs")


MATERIALS_CACHE: dict[str, dict[str, Any]] = {}
POLL_LOCK = False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def log_event(
    level: str,
    event_type: str,
    message: str,
    user_id: int | None = None,
    group_id: int | None = None,
    endpoint: str | None = None,
    http_status: int | None = None,
    response_time: float | None = None,
    polling_duration: float | None = None,
    exception: str | None = None,
) -> None:
    db.session.add(
        LogEntry(
            level=level,
            event_type=event_type,
            message=message,
            user_id=user_id,
            group_id=group_id,
            endpoint=endpoint,
            http_status=http_status,
            response_time=response_time,
            polling_duration=polling_duration,
            exception=exception,
        )
    )
    db.session.commit()


def load_materials_file() -> dict[str, dict[str, Any]]:
    if not MATERIALS_FILE.exists():
        return {}

    raw = json.loads(MATERIALS_FILE.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("materials", [])
    elif isinstance(raw, list):
        items = raw
    else:
        return {}

    materials: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        material_id = str(item.get("materialId") or item.get("id") or item.get("code") or "").strip()
        if not material_id:
            continue
        materials[material_id] = {
            "material_id": material_id,
            "code": item.get("code") or material_id,
            "name": item.get("name") or material_id,
            "credits": item.get("credits"),
            "image": item.get("image") or "",
        }
    return materials


def bootstrap_materials() -> None:
    global MATERIALS_CACHE
    MATERIALS_CACHE = load_materials_file()
    if db.session.query(Material).count():
        return
    for material in MATERIALS_CACHE.values():
        db.session.add(Material(**material))
    db.session.commit()


def ensure_user_name_column() -> None:
    columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(users)"))}
    if "ul_name" in columns:
        return
    db.session.execute(text("ALTER TABLE users ADD COLUMN ul_name VARCHAR(255)"))
    db.session.commit()


def ensure_admin_account() -> None:
    admin = User.query.filter_by(username="admin").first()
    admin = User.query.filter_by(username="admin").first()
    admin_password_hash = generate_password_hash("adminyaali")

    if admin:
        admin.password_hash = admin_password_hash
        admin.is_admin = True
    else:
        admin = User(
            username="admin",
            password_hash=admin_password_hash,
            is_admin=True,
        )
        db.session.add(admin)
    db.session.commit()


def ensure_group_for_class(class_id: str | None) -> Group:
    group_name = f"Class {class_id or 'unassigned'}"
    group = Group.query.filter_by(name=group_name).first()
    if group:
        return group
    group = Group(name=group_name)
    db.session.add(group)
    db.session.commit()
    return group


def material_lookup(material_id: str) -> dict[str, Any]:
    material = Material.query.filter_by(material_id=material_id).first()
    if material:
        return {"code": material.code, "name": material.name, "credits": material.credits, "image": material.image}
    return MATERIALS_CACHE.get(material_id, {"code": material_id, "name": material_id, "credits": None, "image": ""})


def nested_value(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("value") is not None:
            return value.get("value")
        if value.get("rank") is not None:
            return value.get("rank")
    return value


def extract_courses(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [course for course in payload if isinstance(course, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("courses", "grades", "items", "data", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [course for course in value if isinstance(course, dict)]
        if isinstance(value, dict):
            nested = extract_courses(value)
            if nested:
                return nested

    flattened: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, dict):
            flattened.extend(extract_courses(value))
        elif isinstance(value, list):
            flattened.extend([item for item in value if isinstance(item, dict)])
    return flattened


def normalize_snapshot(payload: Any) -> dict[str, Any]:
    courses = extract_courses(payload)
    normalized_courses = []
    for course in courses:
        partial_data = course.get("partial") if isinstance(course.get("partial"), dict) else None
        final_data = course.get("finalGrade") if isinstance(course.get("finalGrade"), dict) else None
        key = str(
            course.get("materialId")
            or course.get("material_id")
            or course.get("course_code")
            or course.get("id")
            or course.get("code")
            or course.get("name")
            or "unknown"
        )
        lookup = material_lookup(key)
        normalized_courses.append(
            {
                "key": key,
                "course_code": course.get("course_code") or course.get("code") or lookup["code"],
                "course_name": course.get("course_name") or course.get("name") or lookup["name"],
                "credits": course.get("credits"),
                "partial": nested_value(course.get("partial")),
                "partial_rank": partial_data.get("rank") if partial_data is not None else nested_value(course.get("rank")),
                "final": nested_value(course.get("final")),
                "final_rank": final_data.get("rank") if final_data is not None else nested_value(course.get("final_rank") or course.get("finalRank") or course.get("finalGradeRank")),
                "finalGrade": nested_value(course.get("finalGrade") if course.get("finalGrade") is not None else course.get("final_grade") or course.get("final")),
            }
        )

    if isinstance(payload, dict):
        student_name = find_nested_string_value(payload, ("student_name", "name", "fullName", "displayName"))
        student_id = payload.get("student_id") or payload.get("studentId")
        class_id = payload.get("class_id") or payload.get("classId")
        average = payload.get("average")
        if isinstance(average, dict):
            average = average.get("partialAverage")
        if average is None and isinstance(payload.get("average"), dict):
            average = payload.get("average", {}).get("value")
        overall_rank = payload.get("overall_rank") or payload.get("rank")
        if isinstance(overall_rank, dict):
            overall_rank = overall_rank.get("partialRank")
        if overall_rank is None and isinstance(payload.get("average"), dict):
            average_block = payload.get("average", {})
            overall_rank = average_block.get("partialRank")
            if overall_rank is None:
                overall_rank = average_block.get("finalGradeRank")
            if overall_rank is None:
                overall_rank = average_block.get("value")
    else:
        student_name = None
        student_id = None
        class_id = None
        average = None
        overall_rank = None

    return {
        "student_name": student_name,
        "student_id": student_id,
        "class_id": class_id,
        "average": average,
        "overall_rank": overall_rank,
        "courses": normalized_courses,
        "raw": payload,
    }


def compare_snapshots(old_snapshot: dict[str, Any] | None, new_snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    old_courses = {course["key"]: course for course in normalize_snapshot(old_snapshot or {}).get("courses", [])}
    new_courses = {course["key"]: course for course in normalize_snapshot(new_snapshot).get("courses", [])}
    changes: list[dict[str, Any]] = []

    for key, new_course in new_courses.items():
        old_course = old_courses.get(key)
        if old_course is None:
            changes.append({"type": "new_course", "course": new_course})
            continue
        for field in ("partial", "partial_rank", "final", "final_rank", "finalGrade"):
            if old_course.get(field) != new_course.get(field):
                changes.append({"type": "field_change", "course": new_course, "field": field, "old": old_course.get(field), "new": new_course.get(field)})

    for key, old_course in old_courses.items():
        if key not in new_courses:
            changes.append({"type": "removed_course", "course": old_course})

    return changes


def grade_to_pass(partial: Any) -> str:
    try:
        partial_value = float(partial)
    except Exception:
        return "Not Available"

    needed = (57 - 0.6 * partial_value) / 0.4
    if needed > partial_value:
        needed = (57 - 0.4 * partial_value) / 0.6

    if needed <= 0:
        return f"Already passed ({needed:.2f})"
    if needed > 100:
        return f"Impossible ({needed:.2f})"
    return f"Need {needed:.2f}"

def activate_ul_cookie(user: User, candidate_cookie: str) -> ULAPIResponse:
    previous_cookie = user.ul_cookie
    user.ul_cookie = candidate_cookie.strip()
    try:
        ensure_ul_identity_from_cookie(user, force=True)
        response = api_request(user)
        if response.status_code == 401:
            raise ULIdentityBootstrapError("That UL cookie is invalid or expired.")
        if response.status_code == 403:
            raise ULIdentityBootstrapError("UL authorization was denied for that cookie.")
        if response.status_code >= 500:
            raise ULIdentityBootstrapError(f"UL server error {response.status_code} while validating that cookie.")
    except Exception:
        user.ul_cookie = previous_cookie
        db.session.commit()
        raise

    payload = response.json_data if isinstance(response.json_data, dict) else {"data": response.json_data}
    user.updated_at = now_utc()
    user.last_request_result = "cookie updated"
    if user.group:
        user.group.paused = False
        user.group.representative_user_id = user.id
    db.session.commit()
    log_event(
        "info",
        "cookie_update",
        f"UL cookie updated for {user.username}",
        user_id=user.id,
        group_id=user.group_id,
        endpoint=response.endpoint,
        http_status=response.status_code,
        response_time=response.response_time,
        polling_duration=response.duration,
    )
    user.last_snapshot = json.dumps(payload, ensure_ascii=False)
    user.last_successful_poll = now_utc()
    db.session.commit()
    return response


def grade_color(value: Any) -> str:
    try:
        number = float(value)
    except Exception:
        return "neutral"
    if number >= 85:
        return "green"
    if number >= 70:
        return "orange"
    return "red"


def api_request(user: User) -> ULAPIResponse:
    if not user.ul_student_id or not user.ul_class_id:
        raise ValueError("Missing UL student or class id")
    return ul_api.get_grades(user.ul_student_id, user.ul_class_id, user.ul_cookie or "")


def find_nested_string_value(payload: Any, candidate_keys: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for key in candidate_keys:
            value = payload.get(key)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    return value
            elif value is not None and not isinstance(value, (dict, list)):
                value_text = str(value).strip()
                if value_text:
                    return value_text
        for value in payload.values():
            nested_value = find_nested_string_value(value, candidate_keys)
            if nested_value:
                return nested_value
    elif isinstance(payload, list):
        for item in payload:
            nested_value = find_nested_string_value(item, candidate_keys)
            if nested_value:
                return nested_value
    return None


def latest_class_id_from_payload(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("classes", "data", "results", "items"):
            nested = payload.get(key)
            if nested is not None:
                class_id = latest_class_id_from_payload(nested)
                if class_id:
                    return class_id
        return None

    if isinstance(payload, list):
        for entry in reversed(payload):
            if not isinstance(entry, dict):
                continue
            class_info = entry.get("class")
            if isinstance(class_info, dict):
                class_id = class_info.get("id") or entry.get("classId")
            else:
                class_id = entry.get("classId")
            if class_id is not None:
                return str(class_id)
    return None


def ensure_ul_identity_from_cookie(user: User, *, force: bool = False) -> bool:
    if not user.ul_cookie:
        raise ULIdentityBootstrapError("No UL cookie is saved for this account.")
    if not force and user.ul_student_id and user.ul_class_id:
        return False

    me_response = ul_api.get_me(user.ul_cookie)
    if me_response.status_code == 401:
        raise ULIdentityBootstrapError("UL cookie is invalid or expired.")
    if me_response.status_code == 403:
        raise ULIdentityBootstrapError("UL authorization was denied for that cookie.")
    if me_response.status_code >= 500:
        raise ULIdentityBootstrapError(f"UL server error {me_response.status_code} while loading profile data.")
    if not isinstance(me_response.json_data, dict):
        raise ULIdentityBootstrapError("UL profile response was not valid JSON.")

    student_username = find_nested_string_value(me_response.json_data, ("username", "userName", "studentUsername", "login")) or ""
    if not student_username:
        raise ULIdentityBootstrapError("UL profile did not include a username.")

    student_name = find_nested_string_value(me_response.json_data, ("name", "fullName", "displayName"))

    classes_response = ul_api.get_student_classes(student_username, user.ul_cookie)
    if classes_response.status_code == 401:
        raise ULIdentityBootstrapError("UL cookie is invalid or expired.")
    if classes_response.status_code == 403:
        raise ULIdentityBootstrapError("UL authorization was denied for that cookie.")
    if classes_response.status_code >= 500:
        raise ULIdentityBootstrapError(f"UL server error {classes_response.status_code} while loading classes.")

    latest_class_id = latest_class_id_from_payload(classes_response.json_data)
    if not latest_class_id:
        raise ULIdentityBootstrapError("UL classes response did not include any classes.")

    previous_group = user.group
    group = ensure_group_for_class(latest_class_id)
    user.ul_student_id = student_username
    if student_name:
        user.ul_name = student_name
    user.ul_class_id = latest_class_id
    user.group_id = group.id
    user.updated_at = now_utc()
    user.last_request_result = "UL identity refreshed"
    group.paused = False
    if group.representative_user_id is None:
        group.representative_user_id = user.id

    if previous_group and previous_group.id != group.id and previous_group.representative_user_id == user.id:
        previous_group.representative_user_id = None

    db.session.commit()
    return True


def representative_for_group(group: Group) -> User | None:
    users = User.query.filter_by(group_id=group.id).order_by(User.last_successful_poll.is_(None), User.last_successful_poll.asc(), User.id.asc()).all()
    valid_users = [user for user in users if user.ul_cookie]
    if not valid_users:
        return None
    if group.representative_user_id:
        current = next((user for user in valid_users if user.id == group.representative_user_id), None)
        if current:
            return current
    return valid_users[0]


def choose_new_representative(group: Group) -> User | None:
    rep = representative_for_group(group)
    group.representative_user_id = rep.id if rep else None
    db.session.commit()
    return rep


def emit_status(group: Group, message: str, level: str = "info") -> None:
    socketio.emit("status_update", {"group_id": group.id, "message": message, "level": level, "timestamp": now_utc().isoformat()}, to=f"group-{group.id}")


def emit_cookie_required(user: User) -> None:
    socketio.emit("cookie_required", {"message": "UL cookie expired. Please paste a new cookie.", "user_id": user.id}, to=f"user-{user.id}")


def summarize_change(changes: list[dict[str, Any]]) -> str:
    if not changes:
        return "Grades updated"
    first = changes[0]
    course = first.get("course", {})
    course_name = course.get("course_name") or course.get("course_code") or "Unknown course"
    if first["type"] == "new_course":
        return f"New course: {course_name}"
    if first["type"] == "removed_course":
        return f"Course removed: {course_name}"
    return f"{course_name} updated"


def emit_grade_change(user: User, changes: list[dict[str, Any]], snapshot: dict[str, Any]) -> None:
    payload = {
        "user_id": user.id,
        "student_name": snapshot.get("student_name"),
        "student_id": user.ul_student_id,
        "class_id": user.ul_class_id,
        "average": snapshot.get("average"),
        "overall_rank": snapshot.get("overall_rank"),
        "changes": changes,
        "courses": snapshot.get("courses", []),
        "timestamp": now_utc().isoformat(),
    }
    socketio.emit("grade_change", payload, to=f"user-{user.id}")
    socketio.emit("toast", {"title": "New grade detected", "message": summarize_change(changes), "kind": "success"}, to=f"user-{user.id}")


def fetch_and_compare_user(user: User) -> dict[str, Any] | None:
    if not user.ul_cookie:
        return None
    started_at = now_utc()
    try:
        response = api_request(user)
    except Exception as exc:
        user.last_request_result = f"error: {exc}"
        db.session.commit()
        log_event(
            "error",
            "poll_failure",
            f"Polling failed for {user.username}: {exc}",
            user_id=user.id,
            group_id=user.group_id,
            endpoint=ul_api.grades_endpoint(user.ul_student_id or "", user.ul_class_id or ""),
            exception=str(exc),
        )
        return None

    if response.status_code == 401:
        user.ul_cookie = None
        user.last_request_result = "401 unauthorized"
        db.session.commit()
        log_event(
            "warning",
            "unauthorized",
            f"UL cookie invalid for {user.username}",
            user_id=user.id,
            group_id=user.group_id,
            endpoint=response.endpoint,
            http_status=response.status_code,
            response_time=response.response_time,
            polling_duration=(now_utc() - started_at).total_seconds(),
        )
        emit_cookie_required(user)
        return None

    if response.status_code == 403:
        user.last_request_result = "403 forbidden"
        db.session.commit()
        log_event(
            "warning",
            "forbidden",
            f"UL authorization denied for {user.username}",
            user_id=user.id,
            group_id=user.group_id,
            endpoint=response.endpoint,
            http_status=response.status_code,
            response_time=response.response_time,
            polling_duration=(now_utc() - started_at).total_seconds(),
        )
        return None

    if response.status_code >= 500:
        user.last_request_result = f"server error {response.status_code}"
        db.session.commit()
        log_event(
            "error",
            "poll_failure",
            f"UL server error for {user.username}: {response.status_code}",
            user_id=user.id,
            group_id=user.group_id,
            endpoint=response.endpoint,
            http_status=response.status_code,
            response_time=response.response_time,
            polling_duration=(now_utc() - started_at).total_seconds(),
        )
        return None

    payload = response.json_data if isinstance(response.json_data, dict) else {"data": response.json_data}
    snapshot = normalize_snapshot(payload)
    old_snapshot = json.loads(user.last_snapshot) if user.last_snapshot else None
    changes = compare_snapshots(old_snapshot, snapshot)

    user.last_snapshot = json.dumps(payload, ensure_ascii=False)
    user.last_successful_poll = now_utc()
    user.last_request_result = "ok"
    db.session.commit()

    log_event(
        "info",
        "poll_success",
        f"Poll succeeded for {user.username}",
        user_id=user.id,
        group_id=user.group_id,
        endpoint=response.endpoint,
        http_status=response.status_code,
        response_time=response.response_time,
        polling_duration=(now_utc() - started_at).total_seconds(),
    )

    if changes:
        emit_grade_change(user, changes, snapshot)
        log_event(
            "info",
            "grade_change",
            summarize_change(changes),
            user_id=user.id,
            group_id=user.group_id,
            endpoint=response.endpoint,
            http_status=response.status_code,
            response_time=response.response_time,
            polling_duration=(now_utc() - started_at).total_seconds(),
        )

    return {"snapshot": snapshot, "changes": changes, "raw": payload}


def poll_group(group: Group) -> None:
    global POLL_LOCK
    if POLL_LOCK:
        return
    POLL_LOCK = True
    started_at = datetime.now(timezone.utc)
    try:
        if group.paused:
            return

        representative = representative_for_group(group) or choose_new_representative(group)
        if not representative:
            group.paused = True
            db.session.commit()
            emit_status(group, "Monitoring paused. No valid UL cookie available.", "warning")
            for user in User.query.filter_by(group_id=group.id).all():
                emit_cookie_required(user)
            log_event("warning", "group_paused", f"Group {group.name} paused: no valid UL cookie", group_id=group.id)
            return

        try:
            response = api_request(representative)
        except Exception as exc:
            group.last_poll = now_utc()
            group.last_response_time = (datetime.now(timezone.utc) - started_at).total_seconds()
            db.session.commit()
            emit_status(group, f"Polling failed for representative {representative.username}: {exc}", "error")
            log_event(
                "error",
                "poll_failure",
                f"Representative poll failed for group {group.name}: {exc}",
                user_id=representative.id,
                group_id=group.id,
                endpoint=ul_api.grades_endpoint(representative.ul_student_id or "", representative.ul_class_id or ""),
                exception=str(exc),
                polling_duration=(datetime.now(timezone.utc) - started_at).total_seconds(),
            )
            return

        group.last_poll = now_utc()
        group.last_response_time = (datetime.now(timezone.utc) - started_at).total_seconds()

        if response.status_code == 401:
            representative.ul_cookie = None
            representative.last_request_result = "401 unauthorized"
            db.session.commit()
            log_event(
                "warning",
                "unauthorized",
                f"Representative cookie invalid for {representative.username}",
                user_id=representative.id,
                group_id=group.id,
                endpoint=response.endpoint,
                http_status=response.status_code,
                response_time=response.response_time,
                polling_duration=(datetime.now(timezone.utc) - started_at).total_seconds(),
            )
            next_rep = choose_new_representative(group)
            if next_rep is None:
                group.paused = True
                db.session.commit()
                emit_status(group, "Monitoring paused. Please update a UL cookie.", "warning")
                for user in User.query.filter_by(group_id=group.id).all():
                    emit_cookie_required(user)
            else:
                emit_status(group, f"Representative switched to {next_rep.username}.", "info")
            return

        if response.status_code == 403:
            representative.last_request_result = "403 forbidden"
            db.session.commit()
            log_event(
                "warning",
                "forbidden",
                f"Representative authorization denied for {representative.username}",
                user_id=representative.id,
                group_id=group.id,
                endpoint=response.endpoint,
                http_status=response.status_code,
                response_time=response.response_time,
                polling_duration=(datetime.now(timezone.utc) - started_at).total_seconds(),
            )
            emit_status(group, "Authorization denied by UL server.", "warning")
            return

        if response.status_code >= 500:
            representative.last_request_result = f"server error {response.status_code}"
            db.session.commit()
            log_event(
                "error",
                "poll_failure",
                f"UL server error for {representative.username}: {response.status_code}",
                user_id=representative.id,
                group_id=group.id,
                endpoint=response.endpoint,
                http_status=response.status_code,
                response_time=response.response_time,
                polling_duration=(datetime.now(timezone.utc) - started_at).total_seconds(),
            )
            return

        payload = response.json_data if isinstance(response.json_data, dict) else {"data": response.json_data}
        snapshot = normalize_snapshot(payload)
        current_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        if group.last_json == current_payload:
            db.session.commit()
            return

        group.last_json = current_payload
        group.last_detected_change = now_utc()
        db.session.commit()
        emit_status(group, f"Change detected for {group.name}. Verifying members.", "info")

        for user in User.query.filter_by(group_id=group.id).all():
            fetch_and_compare_user(user)

    finally:
        POLL_LOCK = False


def poll_all_groups() -> None:
    with app.app_context():
        for group in Group.query.order_by(Group.id.asc()).all():
            poll_group(group)


def is_admin_user() -> bool:
    return bool(current_user.is_authenticated and getattr(current_user, "is_admin", False))


def dashboard_context(user: User) -> dict[str, Any]:
    group = user.group or (ensure_group_for_class(user.ul_class_id) if user.ul_class_id else None)
    snapshot = json.loads(user.last_snapshot) if user.last_snapshot else {"grades": []}
    normalized = normalize_snapshot(snapshot)
    courses = []
    for course in normalized.get("courses", []):
        material = material_lookup(course.get("key") or course.get("course_code") or "")
        final_grade = course.get("finalGrade") if course.get("finalGrade") is not None else course.get("final")
        courses.append(
            {
                **course,
                "code": course.get("course_code") or material.get("code") or "Unknown",
                "name": course.get("course_name") or material.get("name") or "Unknown",
                "credits": course.get("credits") if course.get("credits") is not None else material.get("credits"),
                "partial_color": grade_color(course.get("partial")),
                "final_label": "Final grade" if final_grade is not None else "Grade to pass",
                "final_value": final_grade if final_grade is not None else grade_to_pass(course.get("partial")),
                "final_color": grade_color(final_grade) if final_grade is not None else "neutral",
            }
        )

    return {"user": user, "group": group, "snapshot": normalized, "courses": courses}


def ensure_dashboard_snapshot(user: User) -> None:
    if not user.ul_cookie:
        return
    if user.last_snapshot:
        return
    if not user.ul_student_id or not user.ul_class_id:
        try:
            ensure_ul_identity_from_cookie(user, force=True)
        except ULIdentityBootstrapError:
            return
    fetch_and_compare_user(user)


@login_manager.user_loader
def load_user(user_id: str) -> User | None:
    return db.session.get(User, int(user_id))


@login_manager.unauthorized_handler
def unauthorized_handler():
    return redirect(url_for("login", next=request.path))


@app.before_request
def touch_current_user() -> None:
    if current_user.is_authenticated:
        current_user.last_seen_at = now_utc()
        db.session.commit()


@app.context_processor
def inject_globals() -> dict[str, Any]:
    return {"now": now_utc(), "is_admin": is_admin_user()}


@app.route("/")
def index():
    if current_user.is_authenticated:
        if not current_user.ul_cookie:
            return redirect(url_for("update_cookie"))
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    register_cookie_form = RegisterForm()
    register_credentials_form = RegisterWithCredentialsForm()

    if register_cookie_form.validate_on_submit():
        if User.query.filter_by(username=register_cookie_form.username.data).first():
            flash("Username already exists.", "error")
        else:
            user = User(
                username=register_cookie_form.username.data,
                password_hash=generate_password_hash(register_cookie_form.password.data),
                ul_cookie=register_cookie_form.ul_cookie.data.strip(),
            )
            db.session.add(user)
            db.session.commit()
            login_user(user, remember=register_cookie_form.remember_me.data, fresh=True)
            try:
                ensure_ul_identity_from_cookie(user, force=True)
            except ULIdentityBootstrapError as exc:
                flash(f"UL profile could not be loaded: {exc}", "error")
                return redirect(url_for("update_cookie"))
            log_event("info", "register", f"User {user.username} registered", user_id=user.id, group_id=user.group_id)
            return redirect(url_for("dashboard"))
            
    elif register_credentials_form.validate_on_submit():
        if User.query.filter_by(username=register_credentials_form.username.data).first():
            flash("Username already exists.", "error")
        else:
            try:
                cookie = ul_api.login_with_credentials(register_credentials_form.ul_username.data, register_credentials_form.ul_password.data)
                user = User(
                    username=register_credentials_form.username.data,
                    password_hash=generate_password_hash(register_credentials_form.password.data),
                    ul_cookie=cookie,
                )
                db.session.add(user)
                db.session.commit()
                login_user(user, remember=register_credentials_form.remember_me.data, fresh=True)
                ensure_ul_identity_from_cookie(user, force=True)
                log_event("info", "register", f"User {user.username} registered with credentials", user_id=user.id, group_id=user.group_id)
                return redirect(url_for("dashboard"))
            except Exception as exc:
                flash(f"Registration failed: {exc}", "error")

    return render_template("auth/register.html", cookie_form=register_cookie_form, credentials_form=register_credentials_form)



@app.route("/login", methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data).first()
        if not user or not check_password_hash(user.password_hash, form.password.data):
            flash("Invalid username or password.", "error")
        else:
            login_user(user, remember=form.remember_me.data, fresh=True)
            if not user.ul_student_id or not user.ul_class_id:
                try:
                    ensure_ul_identity_from_cookie(user, force=True)
                except ULIdentityBootstrapError as exc:
                    flash(f"UL profile could not be loaded: {exc}", "error")
                    return redirect(url_for("update_cookie"))
            log_event("info", "login", f"User {user.username} logged in", user_id=user.id, group_id=user.group_id)
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
    return render_template("auth/login.html", form=form)


@app.route("/logout")
@login_required
def logout():
    log_event("info", "logout", f"User {current_user.username} logged out", user_id=current_user.id, group_id=current_user.group_id)
    logout_user()
    return redirect(url_for("login"))


@app.route("/cookie", methods=["GET", "POST"])
@login_required
def update_cookie():
    cookie_form = CookieForm()
    credentials_form = ULCredentialsForm()

    if cookie_form.validate_on_submit():
        try:
            activate_ul_cookie(current_user, cookie_form.ul_cookie.data)
            flash("Cookie verified and monitoring resumed.", "success")
            return redirect(url_for("dashboard"))
        except Exception as exc:
            flash(f"Cookie validation failed: {exc}", "error")
            return render_template("cookie.html", cookie_form=cookie_form, credentials_form=credentials_form)
    
    if credentials_form.validate_on_submit():
        try:
            cookie = ul_api.login_with_credentials(credentials_form.ul_username.data, credentials_form.ul_password.data)
            activate_ul_cookie(current_user, cookie)
            flash("Logged in successfully and monitoring resumed.", "success")
            return redirect(url_for("dashboard"))
        except Exception as exc:
            flash(f"Login failed: {exc}", "error")
            return render_template("cookie.html", cookie_form=cookie_form, credentials_form=credentials_form)

    return render_template("cookie.html", cookie_form=cookie_form, credentials_form=credentials_form)


@app.route("/help")
def help_page():
    return render_template("help.html")


@app.route("/dashboard")
@login_required
def dashboard():
    if not current_user.ul_cookie:
        return redirect(url_for("update_cookie"))
    if not current_user.ul_student_id or not current_user.ul_class_id:
        try:
            ensure_ul_identity_from_cookie(current_user, force=True)
        except ULIdentityBootstrapError as exc:
            flash(f"UL profile could not be loaded: {exc}", "error")
            return redirect(url_for("update_cookie"))
    ensure_dashboard_snapshot(current_user)
    context = dashboard_context(current_user)
    return render_template("dashboard.html", **context)


@app.route("/api/dashboard")
@login_required
def dashboard_api():
    if not current_user.ul_student_id or not current_user.ul_class_id:
        try:
            ensure_ul_identity_from_cookie(current_user, force=True)
        except ULIdentityBootstrapError:
            return jsonify({"error": "UL profile is missing."}), 409
    ensure_dashboard_snapshot(current_user)
    context = dashboard_context(current_user)
    return jsonify(
        {
            "student_name": current_user.ul_name or context["snapshot"].get("student_name"),
            "student_id": current_user.ul_student_id,
            "class_id": current_user.ul_class_id,
            "group": context["group"].name if context["group"] else None,
            "courses": context["courses"],
            "updated_at": current_user.last_successful_poll.isoformat() if current_user.last_successful_poll else None,
        }
    )


@app.route("/admin")
@login_required
def admin_dashboard():
    if not is_admin_user():
        abort(403)
    total_users = User.query.count()
    online_cutoff = now_utc() - timedelta(minutes=2)
    online_users = User.query.filter(User.last_seen_at >= online_cutoff).count()
    groups = Group.query.count()
    requests_per_minute = Setting.query.filter_by(key="requests_per_minute").first()
    last_poll = Group.query.order_by(Group.last_poll.desc().nullslast()).first()
    avg_poll = db.session.query(func.avg(Group.last_response_time)).scalar() or 0
    return render_template(
        "admin/dashboard.html",
        total_users=total_users,
        online_users=online_users,
        groups=groups,
        rpm=requests_per_minute.value if requests_per_minute else "0",
        last_poll=last_poll.last_poll if last_poll else None,
        avg_poll=avg_poll,
        reset_class_cache_form=AdminResetClassCacheForm(),
    )


@app.route("/admin/reset-class-cache", methods=["POST"])
@login_required
def admin_reset_class_cache():
    if not is_admin_user():
        abort(403)
    form = AdminResetClassCacheForm()
    if not form.validate_on_submit():
        abort(400)

    users = User.query.filter(User.ul_class_id.isnot(None)).all()
    groups_touched: set[int] = set()
    for user in users:
        if user.group_id:
            groups_touched.add(user.group_id)
        user.ul_class_id = None
        user.last_snapshot = None
        user.last_successful_poll = None
        user.last_request_result = "class cache reset by admin"

    for group_id in groups_touched:
        group = db.session.get(Group, group_id)
        if not group:
            continue
        group.paused = True
        group.representative_user_id = None
        group.last_json = None
        group.last_poll = None
        group.last_detected_change = None
        group.last_response_time = None

    db.session.commit()
    flash("Cached class IDs were cleared.", "success")
    return redirect(url_for("admin_dashboard"))


@app.route("/admin/users", methods=["GET", "POST"])
@login_required
def admin_users():
    if not is_admin_user():
        abort(403)

    if request.method == "POST":
        action = request.form.get("action")
        user = db.session.get(User, int(request.form.get("user_id", "0")))
        if not user:
            abort(404)
        if action == "delete":
            db.session.delete(user)
            db.session.commit()
            flash("Account deleted.", "success")
        elif action == "reset_cookie":
            user.ul_cookie = None
            user.last_request_result = "cookie reset by admin"
            db.session.commit()
            flash("UL cookie reset.", "success")
            emit_cookie_required(user)
        elif action in {"move_group", "set_group"}:
            group_name = request.form.get("group_name", "").strip()
            if group_name:
                group = Group.query.filter_by(name=group_name).first()
                if not group:
                    group = Group(name=group_name)
                    db.session.add(group)
                    db.session.commit()
                user.group_id = group.id
                user.ul_class_id = request.form.get("class_id") or request.form.get("ul_class_id") or user.ul_class_id
                db.session.commit()
                flash("User moved to another group.", "success")

    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "created_at")
    query = User.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                User.username.ilike(like),
                User.ul_student_id.ilike(like),
                User.ul_class_id.ilike(like),
            )
        )
    sort_map = {
        "username": User.username.asc(),
        "student_id": User.ul_student_id.asc(),
        "class_id": User.ul_class_id.asc(),
        "created_at": User.created_at.desc(),
    }
    users = query.order_by(sort_map.get(sort, User.created_at.desc())).all()
    groups = Group.query.order_by(Group.name.asc()).all()
    return render_template("admin/users.html", users=users, groups=groups, search=search, sort=sort, create_form=AdminCreateUserForm())


@app.route("/admin/users/create", methods=["POST"])
@login_required
def admin_create_user():
    if not is_admin_user():
        abort(403)
    form = AdminCreateUserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data).first():
            flash("Username already exists.", "error")
            return redirect(url_for("admin_users"))
        user = User(
            username=form.username.data,
            password_hash=generate_password_hash(form.password.data),
            ul_cookie=form.ul_cookie.data.strip(),
        )
        db.session.add(user)
        db.session.commit()
        try:
            ensure_ul_identity_from_cookie(user, force=True)
        except ULIdentityBootstrapError as exc:
            flash(f"User created, but UL profile could not be loaded: {exc}", "warning")
            return redirect(url_for("admin_users"))
        flash("User created.", "success")
        return redirect(url_for("admin_users"))
    flash("Invalid data.", "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/groups")
@login_required
def admin_groups():
    if not is_admin_user():
        abort(403)
    group_rows = []
    for group in Group.query.order_by(Group.name.asc()).all():
        members = User.query.filter_by(group_id=group.id).all()
        group_rows.append({"group": group, "members": members, "count": len(members), "last_detected_change": group.last_detected_change})
    return render_template("admin/groups.html", groups=group_rows)


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id: int):
    if not is_admin_user():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    db.session.delete(user)
    db.session.commit()
    flash("Account deleted.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset-cookie", methods=["POST"])
@login_required
def admin_reset_cookie(user_id: int):
    if not is_admin_user():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    user.ul_cookie = None
    user.last_request_result = "cookie reset by admin"
    db.session.commit()
    emit_cookie_required(user)
    flash("UL cookie reset.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/move-group", methods=["POST"])
@login_required
def admin_move_group(user_id: int):
    if not is_admin_user():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    group_name = request.form.get("group_name", "").strip()
    if not group_name:
        abort(400)
    group = Group.query.filter_by(name=group_name).first()
    if not group:
        group = Group(name=group_name)
        db.session.add(group)
        db.session.commit()
    user.group_id = group.id
    db.session.commit()
    flash("User moved.", "success")
    return redirect(url_for("admin_users"))


@socketio.on("connect")
def socket_connect():
    if current_user.is_authenticated:
        join_room(f"user-{current_user.id}")
        if current_user.group_id:
            join_room(f"group-{current_user.group_id}")
        socketio.emit("connection_state", {"connected": True, "message": "Connected"}, to=f"user-{current_user.id}")


@socketio.on("join_dashboard")
def join_dashboard_room():
    if current_user.is_authenticated:
        join_room(f"user-{current_user.id}")
        if current_user.group_id:
            join_room(f"group-{current_user.group_id}")


@socketio.on("request_refresh")
def request_refresh():
    if current_user.is_authenticated:
        socketio.emit("dashboard_payload", dashboard_context(current_user), to=f"user-{current_user.id}")


@app.errorhandler(403)
def forbidden(_error):
    return render_template("errors/403.html"), 403


@app.errorhandler(404)
def not_found(_error):
    return render_template("errors/404.html"), 404


def start_background_services() -> None:
    if not scheduler.running:
        scheduler.add_job(func=poll_all_groups, trigger="interval", seconds=POLL_INTERVAL_SECONDS, id="poll_groups", replace_existing=True, max_instances=1)
        scheduler.start()


with app.app_context():
    db.create_all()
    ensure_user_name_column()
    bootstrap_materials()
    ensure_admin_account()
    start_background_services()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True, use_reloader=False)
