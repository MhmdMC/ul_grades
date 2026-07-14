from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any


def _clear_pycache(root: Path) -> None:
    """Wipe stale __pycache__ dirs on every startup so code changes can't be masked by cached bytecode."""
    for cache_dir in root.rglob("__pycache__"):
        if ".venv" in cache_dir.parts:
            continue
        shutil.rmtree(cache_dir, ignore_errors=True)


_clear_pycache(Path(__file__).resolve().parent)

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
from wtforms.validators import DataRequired, EqualTo, Length

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


class SchoolClass(db.Model):
    """Human-readable identity of a UL class, fetched once per new class id."""

    __tablename__ = "classes"

    class_id = db.Column(db.String(64), primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    year = db.Column(db.Integer, nullable=True)
    half = db.Column(db.String(32), nullable=True)
    major = db.Column(db.String(120), nullable=True)
    branch_number = db.Column(db.Integer, nullable=True)
    branch_name = db.Column(db.String(255), nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class Group(db.Model):
    __tablename__ = "groups"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    class_id = db.Column(db.String(64), nullable=True, index=True)
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
    hostage_consent = db.Column(db.Boolean, default=False, nullable=False)

    group = relationship("Group", back_populates="users", foreign_keys=[group_id])
    ul_credential = relationship("ULCredential", uselist=False, cascade="all, delete-orphan", passive_deletes=False)
    grades = relationship("Grade", cascade="all, delete-orphan", back_populates="user")
    grade_average = relationship("GradeAverage", uselist=False, cascade="all, delete-orphan")
    public_shares = relationship("PublicShare", cascade="all, delete-orphan")


class ULCredential(db.Model):
    __tablename__ = "ul_credentials"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    username = db.Column(db.String(80), nullable=False)
    password = db.Column(db.String(255), nullable=False)


class Grade(db.Model):
    """One row per (user, material). The stored copy is what the UI reads, so a
    page view never costs a UL API call - only the poller writes here."""

    __tablename__ = "grades"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    material_id = db.Column(db.String(64), nullable=False)
    partial = db.Column(db.Float, nullable=True)
    partial_rank = db.Column(db.Integer, nullable=True)
    final = db.Column(db.Float, nullable=True)
    final_grade = db.Column(db.Float, nullable=True)
    final_rank = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    user = relationship("User", back_populates="grades")

    __table_args__ = (db.UniqueConstraint("user_id", "material_id", name="uq_grades_user_material"),)


class GradeAverage(db.Model):
    __tablename__ = "grade_averages"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    partial_average = db.Column(db.Float, nullable=True)
    partial_rank = db.Column(db.Integer, nullable=True)
    final_average = db.Column(db.Float, nullable=True)
    final_rank = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class PublicShare(db.Model):
    """Presence of a row means that one item is public. No row means private."""

    __tablename__ = "public_shares"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    item_key = db.Column(db.String(120), primary_key=True)


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
    hostage_consent = BooleanField(
        "By leaving this on you consent to have your grades taken hostage.",
        default=False,
    )
    submit = SubmitField("Create account")


class RegisterWithCredentialsForm(AuthForm):
    ul_username = StringField("UL Username", validators=[DataRequired(), Length(min=3, max=80)])
    ul_password = PasswordField("UL Password", validators=[DataRequired(), Length(min=6, max=128)])
    remember_me = BooleanField("Remember me", default=True)
    hostage_consent = BooleanField(
        "By leaving this on you consent to have your grades taken hostage.",
        default=False,
    )
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


class AdminChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), Length(min=6, max=128)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    submit = SubmitField("Change password")


class ConsentForm(FlaskForm):
    hostage_consent = BooleanField(
        "By leaving this on you consent to have your grades taken hostage.",
        default=False,
    )
    submit = SubmitField("Save")


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
    if "ul_name" not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN ul_name VARCHAR(255)"))
    if "hostage_consent" not in columns:
        db.session.execute(text("ALTER TABLE users ADD COLUMN hostage_consent BOOLEAN NOT NULL DEFAULT 0"))

    group_columns = {row[1] for row in db.session.execute(text("PRAGMA table_info(groups)"))}
    if "class_id" not in group_columns:
        db.session.execute(text("ALTER TABLE groups ADD COLUMN class_id VARCHAR(64)"))
    db.session.commit()


def ensure_admin_account() -> None:
    # Only bootstrap the admin account once. After that, the real password
    # lives only as a hash in the database (see /admin/change-password) -
    # never overwrite it here, or a password change would be undone on restart.
    if User.query.filter_by(username="admin").first():
        return
    initial_password = os.environ.get("ADMIN_INITIAL_PASSWORD", "adminyaali")
    admin = User(
        username="admin",
        password_hash=generate_password_hash(initial_password),
        is_admin=True,
    )
    db.session.add(admin)
    db.session.commit()


ORDINAL_HALVES = {"fall": "Fall", "spring": "Spring", "summer": "Summer", "winter": "Winter"}


def build_class_name(payload: dict[str, Any]) -> str | None:
    """Year 2 - Spring - Major - Branch 3   (the major segment is dropped when null)."""
    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    branch = payload.get("branch") if isinstance(payload.get("branch"), dict) else {}

    year = definition.get("year")
    half = definition.get("half")
    major = definition.get("major")
    branch_number = branch.get("number")

    parts: list[str] = []
    if year is not None:
        parts.append(f"Year {year}")
    if half:
        parts.append(ORDINAL_HALVES.get(str(half).lower(), str(half).title()))
    if major:
        parts.append(str(major))
    if branch_number is not None:
        parts.append(f"Branch {branch_number}")

    return " - ".join(parts) if parts else None


def ensure_class_record(class_id: str, user: User) -> SchoolClass | None:
    """Resolve a class id to its real name. Only calls UL the first time we see the class."""
    if not class_id:
        return None

    record = db.session.get(SchoolClass, str(class_id))
    if record:
        return record
    if not user.ul_student_id or not user.ul_cookie:
        return None

    try:
        response = ul_api.get_current_class(user.ul_student_id, user.ul_cookie)
    except Exception as exc:
        log_event("warning", "class_lookup_failed", f"Class lookup failed for {class_id}: {exc}", user_id=user.id)
        return None

    if response.status_code >= 400 or not isinstance(response.json_data, dict):
        log_event(
            "warning",
            "class_lookup_failed",
            f"Class lookup for {class_id} returned {response.status_code}",
            user_id=user.id,
            endpoint=response.endpoint,
            http_status=response.status_code,
        )
        return None

    payload = response.json_data
    name = build_class_name(payload)
    if not name:
        return None

    # Trust the id the API reports over the one we guessed from the classes list.
    resolved_id = str(payload.get("id") or class_id)
    record = db.session.get(SchoolClass, resolved_id)
    if record is None:
        record = SchoolClass(class_id=resolved_id)
        db.session.add(record)

    definition = payload.get("definition") if isinstance(payload.get("definition"), dict) else {}
    branch = payload.get("branch") if isinstance(payload.get("branch"), dict) else {}
    record.name = name
    record.year = coerce_int(definition.get("year"))
    record.half = definition.get("half")
    record.major = definition.get("major")
    record.branch_number = coerce_int(branch.get("number"))
    record.branch_name = branch.get("name")
    record.updated_at = now_utc()
    db.session.commit()

    log_event("info", "class_resolved", f"Class {resolved_id} resolved to '{name}'", user_id=user.id)
    return record


def class_display_name(class_id: str | None) -> str:
    if not class_id:
        return "Unassigned"
    record = db.session.get(SchoolClass, str(class_id))
    return record.name if record else f"Class {class_id}"


def ensure_group_for_class(class_id: str | None, user: User | None = None) -> Group:
    key = str(class_id) if class_id else None

    if user is not None and key:
        ensure_class_record(key, user)

    group = Group.query.filter_by(class_id=key).first() if key else None
    if group is None:
        # Fall back to the legacy name-keyed lookup so pre-existing groups get adopted
        # rather than duplicated, then backfill their class_id.
        group = Group.query.filter_by(name=f"Class {key or 'unassigned'}").first()
        if group is not None and key:
            group.class_id = key

    desired_name = class_display_name(key) if key else "Unassigned"

    if group is None:
        # Group.name is unique; keep the class id as a suffix if the name is taken.
        name = desired_name
        if Group.query.filter_by(name=name).first():
            name = f"{desired_name} ({key})"
        group = Group(name=name, class_id=key)
        db.session.add(group)
    elif group.name != desired_name and not Group.query.filter(Group.name == desired_name, Group.id != group.id).first():
        group.name = desired_name

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
        average_block = payload.get("average")
        if not isinstance(average_block, dict):
            average_block = {}

        average = average_block.get("partialAverage")
        if average is None:
            average = average_block.get("value")

        overall_rank = average_block.get("partialRank")
        if overall_rank is None:
            overall_rank = average_block.get("rank")

        final_average = average_block.get("finalGradeAverage")
        final_rank = average_block.get("finalGradeRank")
    else:
        student_name = None
        student_id = None
        class_id = None
        average = None
        overall_rank = None
        final_average = None
        final_rank = None

    # UL's API reports 0 / 0.0 (instead of null) for the final average/rank
    # before any final grades exist - treat that the same as "not available",
    # same as a null finalGrade on an individual course.
    if not final_average:
        final_average = None
    if not final_rank:
        final_rank = None

    return {
        "student_name": student_name,
        "student_id": student_id,
        "class_id": class_id,
        "average": average,
        "overall_rank": overall_rank,
        "final_average": final_average,
        "final_rank": final_rank,
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

def save_ul_credentials(user: User, ul_username: str, ul_password: str) -> None:
    credential = ULCredential.query.filter_by(user_id=user.id).first()
    if credential:
        credential.username = ul_username
        credential.password = ul_password
    else:
        credential = ULCredential(user_id=user.id, username=ul_username, password=ul_password)
        db.session.add(credential)
    db.session.commit()


def refresh_cookie_from_credentials(user: User) -> bool:
    credential = ULCredential.query.filter_by(user_id=user.id).first()
    if not credential:
        return False
    try:
        new_cookie = ul_api.login_with_credentials(credential.username, credential.password)
    except Exception as exc:
        log_event(
            "warning",
            "cookie_auto_refresh_failed",
            f"Stored UL credentials failed to refresh cookie for {user.username}: {exc}",
            user_id=user.id,
            group_id=user.group_id,
        )
        return False

    user.ul_cookie = new_cookie
    user.last_request_result = "cookie auto-refreshed from stored UL credentials"
    db.session.commit()
    log_event(
        "info",
        "cookie_auto_refresh",
        f"Auto-refreshed UL cookie for {user.username} using stored credentials",
        user_id=user.id,
        group_id=user.group_id,
    )
    return True


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
    persist_snapshot(user, normalize_snapshot(payload))
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


# Every individually shareable item. Each (field, label) pair becomes one
# checkbox on the sharing page and one possible row on the public profile.
COURSE_SHARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("partial", "Partial grade"),
    ("partial_rank", "Partial rank"),
    ("final", "Final grade"),
    ("final_rank", "Final rank"),
)
OVERALL_SHARE_FIELDS: tuple[tuple[str, str], ...] = (
    ("partial_average", "Partial average"),
    ("partial_rank", "Partial rank"),
    ("final_average", "Final average"),
    ("final_rank", "Final rank"),
)


def course_share_key(material_id: Any, field: str) -> str:
    return f"course:{material_id}:{field}"


def overall_share_key(field: str) -> str:
    return f"overall:{field}"


def coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def persist_snapshot(user: User, normalized: dict[str, Any]) -> None:
    """Write a freshly fetched snapshot into the grades tables."""
    existing = {grade.material_id: grade for grade in Grade.query.filter_by(user_id=user.id).all()}
    seen: set[str] = set()

    for course in normalized.get("courses", []):
        material_id = str(course.get("key") or "").strip()
        if not material_id:
            continue
        seen.add(material_id)
        grade = existing.get(material_id)
        if grade is None:
            grade = Grade(user_id=user.id, material_id=material_id)
            db.session.add(grade)
        grade.partial = coerce_float(course.get("partial"))
        grade.partial_rank = coerce_int(course.get("partial_rank"))
        grade.final = coerce_float(course.get("final"))
        grade.final_grade = coerce_float(course.get("finalGrade"))
        grade.final_rank = coerce_int(course.get("final_rank"))
        grade.updated_at = now_utc()

    for material_id, grade in existing.items():
        if material_id not in seen:
            db.session.delete(grade)

    average = GradeAverage.query.filter_by(user_id=user.id).first()
    if average is None:
        average = GradeAverage(user_id=user.id)
        db.session.add(average)
    average.partial_average = coerce_float(normalized.get("average"))
    average.partial_rank = coerce_int(normalized.get("overall_rank"))
    average.final_average = coerce_float(normalized.get("final_average"))
    average.final_rank = coerce_int(normalized.get("final_rank"))
    average.updated_at = now_utc()

    db.session.commit()


def stored_snapshot(user: User) -> dict[str, Any] | None:
    """Rebuild a normalized snapshot from the database. Never calls the UL API."""
    grades = Grade.query.filter_by(user_id=user.id).order_by(Grade.material_id.asc()).all()
    if not grades:
        return None

    average = GradeAverage.query.filter_by(user_id=user.id).first()
    courses: list[dict[str, Any]] = []
    for grade in grades:
        lookup = material_lookup(grade.material_id)
        courses.append(
            {
                "key": grade.material_id,
                "course_code": lookup["code"],
                "course_name": lookup["name"],
                "credits": lookup["credits"],
                "partial": grade.partial,
                "partial_rank": grade.partial_rank,
                "final": grade.final,
                "final_rank": grade.final_rank,
                "finalGrade": grade.final_grade,
            }
        )

    return {
        "student_name": user.ul_name,
        "student_id": user.ul_student_id,
        "class_id": user.ul_class_id,
        "average": average.partial_average if average else None,
        "overall_rank": average.partial_rank if average else None,
        "final_average": average.final_average if average else None,
        "final_rank": average.final_rank if average else None,
        "courses": courses,
        "updated_at": max((grade.updated_at for grade in grades), default=None),
        "raw": None,
    }


def public_keys_for(user: User) -> set[str]:
    return {row.item_key for row in PublicShare.query.filter_by(user_id=user.id).all()}


def allowed_share_keys(user: User) -> set[str]:
    """The keys this user is actually allowed to publish, derived from their own grades."""
    keys = {overall_share_key(field) for field, _ in OVERALL_SHARE_FIELDS}
    normalized = stored_snapshot(user) or {}
    for course in normalized.get("courses", []):
        for field, _ in COURSE_SHARE_FIELDS:
            keys.add(course_share_key(course["key"], field))
    return keys


def set_public_keys(user: User, keys: set[str]) -> None:
    current = {row.item_key: row for row in PublicShare.query.filter_by(user_id=user.id).all()}
    for key in keys - set(current):
        db.session.add(PublicShare(user_id=user.id, item_key=key))
    for key in set(current) - keys:
        db.session.delete(current[key])
    db.session.commit()


def course_field_values(course: dict[str, Any]) -> dict[str, Any]:
    final_grade = course.get("finalGrade") if course.get("finalGrade") is not None else course.get("final")
    return {
        "partial": course.get("partial"),
        "partial_rank": course.get("partial_rank"),
        "final": final_grade,
        "final_rank": course.get("final_rank"),
    }


def overall_field_values(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "partial_average": snapshot.get("average"),
        "partial_rank": snapshot.get("overall_rank"),
        "final_average": snapshot.get("final_average"),
        "final_rank": snapshot.get("final_rank"),
    }


OVERALL_SUBJECT = "overall"
LEADERBOARD_KINDS: tuple[tuple[str, str], ...] = (("partial", "Partial"), ("final", "Final"))


def leaderboard_subjects(class_id: str) -> list[dict[str, Any]]:
    """What can be ranked in a class: the overall average, plus every material
    anyone in the class actually has. Partial vs final is picked separately."""
    subjects: list[dict[str, Any]] = [{"id": OVERALL_SUBJECT, "name": "Overall average"}]

    user_ids = [user.id for user in User.query.filter_by(ul_class_id=str(class_id), is_admin=False).all()]
    if not user_ids:
        return subjects

    material_ids = {grade.material_id for grade in Grade.query.filter(Grade.user_id.in_(user_ids)).all()}
    materials = sorted(
        ((material_id, material_lookup(material_id)) for material_id in material_ids),
        key=lambda item: str(item[1]["name"]).lower(),
    )
    subjects.extend({"id": material_id, "name": lookup["name"]} for material_id, lookup in materials)
    return subjects


def metric_key_for(subject: str, kind: str) -> str | None:
    """Fold the two dropdowns back into one share key, so leaderboard visibility
    and the sharing system stay keyed the same way."""
    if kind not in {key for key, _ in LEADERBOARD_KINDS}:
        return None
    if subject == OVERALL_SUBJECT:
        return overall_share_key(f"{kind}_average")
    return course_share_key(subject, kind)


def metric_value_for_user(user: User, metric_key: str) -> float | None:
    if metric_key.startswith("overall:"):
        field = metric_key.split(":", 1)[1]
        average = GradeAverage.query.filter_by(user_id=user.id).first()
        if average is None:
            return None
        return {"partial_average": average.partial_average, "final_average": average.final_average}.get(field)

    if metric_key.startswith("course:"):
        parts = metric_key.split(":", 2)
        if len(parts) != 3:
            return None
        _, material_id, field = parts
        grade = Grade.query.filter_by(user_id=user.id, material_id=material_id).first()
        if grade is None:
            return None
        if field == "partial":
            return grade.partial
        if field == "final":
            return grade.final_grade if grade.final_grade is not None else grade.final
    return None


def build_leaderboard(class_id: str, metric_key: str, viewer: User) -> list[dict[str, Any]]:
    """Grades are always visible; the *name* attached to them is not.

    A name is revealed only when the student published that exact grade. Admins
    additionally see the names of students who consented to the hostage clause.
    You always see your own row.
    """
    viewer_is_admin = bool(getattr(viewer, "is_admin", False))
    students = User.query.filter_by(ul_class_id=str(class_id), is_admin=False).all()

    rows: list[dict[str, Any]] = []
    for student in students:
        value = metric_value_for_user(student, metric_key)
        if value is None:
            continue

        is_public = metric_key in public_keys_for(student)
        is_self = student.id == viewer.id
        revealed = is_public or is_self or (viewer_is_admin and student.hostage_consent)

        if is_self:
            reveal_reason = "you"
        elif is_public:
            reveal_reason = "public"
        elif revealed:
            reveal_reason = "consent"
        else:
            reveal_reason = None

        rows.append(
            {
                "user": student if revealed else None,
                "name": (student.ul_name or student.username) if revealed else "Anonymous",
                "username": student.username if revealed else None,
                "revealed": revealed,
                "reveal_reason": reveal_reason,
                "is_self": is_self,
                "value": value,
                "color": grade_color(value),
                # A revealed name means that grade is already public, so linking
                # to the profile exposes nothing new.
                "profile_url": url_for("public_profile_page", username=student.username) if is_public else None,
            }
        )

    rows.sort(key=lambda row: row["value"], reverse=True)

    # Standard competition ranking: equal values share a rank.
    previous_value = None
    previous_rank = 0
    for index, row in enumerate(rows, start=1):
        if previous_value is not None and row["value"] == previous_value:
            row["rank"] = previous_rank
        else:
            row["rank"] = index
            previous_rank = index
            previous_value = row["value"]
    return rows


def viewable_profile(user: User, viewer: User) -> dict[str, Any] | None:
    """The full grade sheet, with each field marked visible or hidden.

    A hidden field carries value=None: the real number never reaches the template,
    so it cannot be recovered from the HTML. The blur is only cosmetic.
    """
    normalized = stored_snapshot(user)
    if normalized is None:
        return None

    keys = public_keys_for(user)
    is_self = viewer.id == user.id
    # Admins may additionally see the grades of students who consented at register.
    consent_override = bool(getattr(viewer, "is_admin", False)) and user.hostage_consent

    def is_visible(key: str) -> bool:
        return is_self or consent_override or key in keys

    overall_values = overall_field_values(normalized)
    overall: list[dict[str, Any]] = []
    for field, label in OVERALL_SHARE_FIELDS:
        visible = is_visible(overall_share_key(field))
        overall.append(
            {
                "label": label,
                "visible": visible,
                "value": overall_values.get(field) if visible else None,
            }
        )

    courses: list[dict[str, Any]] = []
    for course in normalized.get("courses", []):
        values = course_field_values(course)
        fields: list[dict[str, Any]] = []
        for field, label in COURSE_SHARE_FIELDS:
            visible = is_visible(course_share_key(course["key"], field))
            value = values.get(field) if visible else None
            fields.append(
                {
                    "label": label,
                    "visible": visible,
                    "value": value,
                    "color": grade_color(value) if visible and field in ("partial", "final") else "neutral",
                }
            )
        courses.append({"code": course.get("course_code"), "name": course.get("course_name"), "fields": fields})

    visible_count = sum(1 for item in overall if item["visible"])
    visible_count += sum(1 for course in courses for field in course["fields"] if field["visible"])
    total_count = len(overall) + sum(len(course["fields"]) for course in courses)

    return {
        "user": user,
        "overall": overall,
        "courses": courses,
        "class_name": class_display_name(user.ul_class_id),
        "visible_count": visible_count,
        "total_count": total_count,
        "revealed_by_consent": consent_override and not is_self,
        "is_self": is_self,
    }


def search_students(query: str, limit: int = 40) -> list[dict[str, Any]]:
    """Match on the last 4 digits of the UL id, or on the UL name."""
    needle = query.strip().lower()
    if not needle:
        return []

    candidates = User.query.filter(User.is_admin.is_(False), User.ul_student_id.isnot(None)).all()
    results: list[dict[str, Any]] = []
    for student in candidates:
        last4 = str(student.ul_student_id or "")[-4:]
        by_id = needle.isdigit() and needle in last4
        by_name = bool(student.ul_name) and needle in student.ul_name.lower()
        if not (by_id or by_name):
            continue
        results.append(
            {
                "user": student,
                "name": student.ul_name or student.username,
                "last4": last4,
                "class_name": class_display_name(student.ul_class_id),
                "public_count": len(public_keys_for(student)),
            }
        )

    results.sort(key=lambda item: str(item["name"]).lower())
    return results[:limit]


def public_profile(user: User) -> dict[str, Any] | None:
    """Only the items this user explicitly marked public. Returns None if nothing is shared."""
    keys = public_keys_for(user)
    normalized = stored_snapshot(user)
    if not keys or normalized is None:
        return None

    values = overall_field_values(normalized)
    overall = [
        {"label": label, "value": values.get(field), "field": field}
        for field, label in OVERALL_SHARE_FIELDS
        if overall_share_key(field) in keys
    ]

    courses: list[dict[str, Any]] = []
    for course in normalized.get("courses", []):
        course_values = course_field_values(course)
        # "fields" and not "items": Jinja resolves course.items to dict.items first.
        fields = [
            {
                "label": label,
                "value": course_values.get(field),
                "color": grade_color(course_values.get(field)) if field in ("partial", "final") else "neutral",
            }
            for field, label in COURSE_SHARE_FIELDS
            if course_share_key(course["key"], field) in keys
        ]
        if fields:
            courses.append({"code": course.get("course_code"), "name": course.get("course_name"), "fields": fields})

    if not overall and not courses:
        return None

    return {
        "user": user,
        "overall": overall,
        "courses": courses,
        "updated_at": normalized.get("updated_at"),
    }


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
    if me_response.status_code == 401 and refresh_cookie_from_credentials(user):
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
    if classes_response.status_code == 401 and refresh_cookie_from_credentials(user):
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
    # Set the student id first: resolving the class name is a per-student UL call.
    user.ul_student_id = student_username
    group = ensure_group_for_class(latest_class_id, user)
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
        "final_average": snapshot.get("final_average"),
        "final_rank": snapshot.get("final_rank"),
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

    if response.status_code == 401 and refresh_cookie_from_credentials(user):
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
    persist_snapshot(user, snapshot)

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

        if response.status_code == 401 and refresh_cookie_from_credentials(representative):
            try:
                response = api_request(representative)
            except Exception:
                pass

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

    # Read the stored copy. Falling back to last_snapshot only matters for a user
    # whose grades were fetched before the grades tables existed.
    normalized = stored_snapshot(user)
    if normalized is None:
        raw = json.loads(user.last_snapshot) if user.last_snapshot else {"grades": []}
        normalized = normalize_snapshot(raw)

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
                # UL sends 0 instead of null for a final rank that does not exist yet.
                "final_rank": course.get("final_rank") or None,
            }
        )

    return {"user": user, "group": group, "snapshot": normalized, "courses": courses}


def ensure_dashboard_snapshot(user: User) -> None:
    """Fetch from UL only when we have nothing stored yet. Once grades are in the
    database, the poller is the only thing that refreshes them - viewing a page
    never costs a UL API call."""
    if not user.ul_cookie:
        return
    if Grade.query.filter_by(user_id=user.id).first():
        return
    if user.last_snapshot:
        return
    if not user.ul_student_id or not user.ul_class_id:
        try:
            ensure_ul_identity_from_cookie(user, force=True)
        except ULIdentityBootstrapError:
            return
    fetch_and_compare_user(user)


def student_only(view):
    """Admins are staff, not students: they have no cookie, grades, or public page."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_admin_user():
            return redirect(url_for("admin_dashboard"))
        return view(*args, **kwargs)

    return wrapper


def backfill_stored_grades() -> None:
    """One-time move of pre-existing last_snapshot JSON into the grades tables."""
    users = User.query.filter(User.last_snapshot.isnot(None)).all()
    for user in users:
        if Grade.query.filter_by(user_id=user.id).first():
            continue
        try:
            payload = json.loads(user.last_snapshot)
        except (TypeError, ValueError):
            continue
        persist_snapshot(user, normalize_snapshot(payload))


def backfill_class_names() -> None:
    """Resolve names for classes that predate the classes table. One UL call per
    unknown class, then never again."""
    users = User.query.filter(User.ul_class_id.isnot(None), User.ul_cookie.isnot(None)).all()
    resolved: set[str] = set()
    for user in users:
        class_id = str(user.ul_class_id)
        if class_id in resolved or db.session.get(SchoolClass, class_id):
            continue
        record = ensure_class_record(class_id, user)
        if record:
            resolved.add(record.class_id)
            ensure_group_for_class(class_id)


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
    if not current_user.is_authenticated:
        return redirect(url_for("login"))
    if is_admin_user():
        return redirect(url_for("admin_dashboard"))
    if not current_user.ul_cookie:
        return redirect(url_for("update_cookie"))
    return redirect(url_for("dashboard"))


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
                hostage_consent=register_cookie_form.hostage_consent.data,
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
                    hostage_consent=register_credentials_form.hostage_consent.data,
                )
                db.session.add(user)
                db.session.commit()
                save_ul_credentials(user, register_credentials_form.ul_username.data, register_credentials_form.ul_password.data)
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
@student_only
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
            save_ul_credentials(current_user, credentials_form.ul_username.data, credentials_form.ul_password.data)
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
@student_only
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
    consent_form = ConsentForm(hostage_consent=current_user.hostage_consent)
    return render_template("dashboard.html", **context, consent_form=consent_form)


@app.route("/consent", methods=["POST"])
@login_required
@student_only
def update_consent():
    form = ConsentForm()
    if form.validate_on_submit():
        current_user.hostage_consent = form.hostage_consent.data
        db.session.commit()
        flash("Preference updated.", "success")
    return redirect(url_for("dashboard"))


@app.route("/share", methods=["GET", "POST"])
@login_required
@student_only
def share_settings():
    if request.method == "POST":
        requested = set(request.form.getlist("public"))
        set_public_keys(current_user, requested & allowed_share_keys(current_user))
        flash("Sharing preferences saved.", "success")
        return redirect(url_for("share_settings"))

    ensure_dashboard_snapshot(current_user)
    snapshot = stored_snapshot(current_user) or {"courses": []}
    keys = public_keys_for(current_user)

    overall_values = overall_field_values(snapshot)
    overall_rows = [
        {
            "key": overall_share_key(field),
            "label": label,
            "value": overall_values.get(field),
            "is_public": overall_share_key(field) in keys,
        }
        for field, label in OVERALL_SHARE_FIELDS
    ]

    course_rows = []
    for course in snapshot.get("courses", []):
        values = course_field_values(course)
        course_rows.append(
            {
                "code": course.get("course_code"),
                "name": course.get("course_name"),
                "fields": [
                    {
                        "key": course_share_key(course["key"], field),
                        "label": label,
                        "value": values.get(field),
                        "is_public": course_share_key(course["key"], field) in keys,
                    }
                    for field, label in COURSE_SHARE_FIELDS
                ],
            }
        )

    return render_template(
        "share.html",
        overall_rows=overall_rows,
        course_rows=course_rows,
        public_count=len(keys),
    )


@app.route("/leaderboard")
@login_required
def leaderboard():
    search = request.args.get("q", "").strip()

    # Classes that actually have students to rank.
    ranked_class_ids = {
        row[0]
        for row in db.session.query(User.ul_class_id)
        .filter(User.ul_class_id.isnot(None), User.is_admin.is_(False))
        .distinct()
        .all()
        if row[0]
    }
    classes = [{"class_id": class_id, "name": class_display_name(class_id)} for class_id in ranked_class_ids]
    if search:
        needle = search.lower()
        classes = [item for item in classes if needle in item["name"].lower() or needle in str(item["class_id"])]
    classes.sort(key=lambda item: item["name"])

    selected_class_id = request.args.get("class_id") or ""
    if not selected_class_id and not search and current_user.ul_class_id:
        selected_class_id = str(current_user.ul_class_id)
    if selected_class_id and selected_class_id not in ranked_class_ids:
        selected_class_id = ""

    subjects: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    selected_subject = OVERALL_SUBJECT
    selected_kind = "partial"

    if selected_class_id:
        subjects = leaderboard_subjects(selected_class_id)
        valid_subjects = {subject["id"] for subject in subjects}

        selected_subject = request.args.get("subject") or OVERALL_SUBJECT
        if selected_subject not in valid_subjects:
            selected_subject = OVERALL_SUBJECT

        selected_kind = request.args.get("kind") or "partial"
        if selected_kind not in {key for key, _ in LEADERBOARD_KINDS}:
            selected_kind = "partial"

        metric_key = metric_key_for(selected_subject, selected_kind)
        if metric_key:
            rows = build_leaderboard(selected_class_id, metric_key, current_user)

    return render_template(
        "leaderboard.html",
        search=search,
        classes=classes,
        selected_class_id=selected_class_id,
        selected_class_name=class_display_name(selected_class_id) if selected_class_id else None,
        subjects=subjects,
        kinds=LEADERBOARD_KINDS,
        selected_subject=selected_subject,
        selected_kind=selected_kind,
        rows=rows,
        revealed_count=sum(1 for row in rows if row["revealed"]),
    )


@app.route("/public")
@login_required
def public_directory():
    shared_user_ids = {row.user_id for row in PublicShare.query.all()}
    if not shared_user_ids:
        return render_template("public/directory.html", classes=[])

    users = User.query.filter(User.id.in_(shared_user_ids)).order_by(User.username.asc()).all()

    # Bucket every sharing student under the class they belong to.
    buckets: dict[str | None, list[dict[str, Any]]] = {}
    for user in users:
        profile = public_profile(user)
        if not profile:
            continue
        buckets.setdefault(user.ul_class_id, []).append(
            {
                "user": user,
                "course_count": len(profile["courses"]),
                "overall_count": len(profile["overall"]),
            }
        )

    classes = [
        {
            "class_id": class_id,
            "name": class_display_name(class_id),
            "students": sorted(entries, key=lambda entry: entry["user"].username.lower()),
        }
        for class_id, entries in buckets.items()
    ]
    classes.sort(key=lambda item: item["name"])
    return render_template("public/directory.html", classes=classes)


@app.route("/search")
@login_required
def search_page():
    query = request.args.get("q", "").strip()
    return render_template("public/search.html", search=query, results=search_students(query) if query else [])


@app.route("/u/<username>")
@login_required
def public_profile_page(username: str):
    user = User.query.filter_by(username=username).first()
    if not user or user.is_admin:
        abort(404)
    profile = viewable_profile(user, current_user)
    if not profile:
        abort(404)
    return render_template("public/profile.html", profile=profile)


@app.route("/api/dashboard")
@login_required
@student_only
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
            "average": context["snapshot"].get("average"),
            "overall_rank": context["snapshot"].get("overall_rank"),
            "final_average": context["snapshot"].get("final_average"),
            "final_rank": context["snapshot"].get("final_rank"),
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
        change_password_form=AdminChangePasswordForm(),
    )


@app.route("/admin/change-password", methods=["POST"])
@login_required
def admin_change_password():
    if not is_admin_user():
        abort(403)
    form = AdminChangePasswordForm()
    if form.validate_on_submit():
        if not check_password_hash(current_user.password_hash, form.current_password.data):
            flash("Current password is incorrect.", "error")
        else:
            current_user.password_hash = generate_password_hash(form.new_password.data)
            db.session.commit()
            log_event(
                "info",
                "admin_password_change",
                f"Admin {current_user.username} changed their password",
                user_id=current_user.id,
            )
            flash("Password updated.", "success")
    else:
        flash("Could not update password. Check the form.", "error")
    return redirect(url_for("admin_dashboard"))


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


@app.route("/admin/users/<int:user_id>/dashboard")
@login_required
def admin_view_dashboard(user_id: int):
    if not is_admin_user():
        abort(403)
    user = db.session.get(User, user_id)
    if not user:
        abort(404)
    if not user.hostage_consent:
        abort(403)
    context = dashboard_context(user)
    return render_template("dashboard.html", **context, admin_viewing=True)


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


@socketio.on("admin_test_alarm")
def admin_test_alarm():
    """Sound the grade-change alarm on every connected client at once.

    The button is already template-guarded for admins only. Socket.IO
    events carry no CSRF token, but the worst a malicious emit can do
    is a harmless 3-second beep on every connected device.
    """
    log_event(
        "info",
        "admin_test_alarm",
        f"Admin test alarm triggered by {'admin' if is_admin_user() else 'unknown'}",
    )
    socketio.emit(
        "play_alarm",
        {
            "seconds": 3,
            "title": "Sound test",
            "message": "An admin triggered the grade-change alarm. This is not a grade change.",
        },
    )


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
    backfill_stored_grades()
    backfill_class_names()
    start_background_services()


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True, use_reloader=False)
