# UL Grades Database Schema Reference

**Source file:** `app.py`  
**Repository:** `MhmdMC/ul_grades`  
**Source commit:** `0bc74372c87eafce2583bf58bbbf1ff9314ea232`

## 1. Database configuration

```python
DB_FILE = BASE_DIR / "ul_grades.sqlite3"

app.config.update(
    SQLALCHEMY_DATABASE_URI=f"sqlite:///{DB_FILE}",
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
)

db = SQLAlchemy(app)
```

The application uses SQLite through Flask-SQLAlchemy. The database file is `ul_grades.sqlite3` in the application directory.

## 2. SQLAlchemy tables

Every class inheriting from `db.Model` defines a database table.

### `classes`

Defined by `SchoolClass`:

| Column | Type | Constraints / notes |
|---|---|---|
| `class_id` | `VARCHAR(64)` | Primary key |
| `name` | `VARCHAR(255)` | Not null |
| `year` | `INTEGER` | Nullable |
| `half` | `VARCHAR(32)` | Nullable |
| `major` | `VARCHAR(120)` | Nullable |
| `branch_number` | `INTEGER` | Nullable |
| `branch_name` | `VARCHAR(255)` | Nullable |
| `language` | `VARCHAR(16)` | Nullable |
| `updated_at` | `DATETIME` | Not null; UTC default |

### `groups`

Defined by `Group`:

| Column | Type | Constraints / notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `name` | `VARCHAR(120)` | Unique, not null |
| `class_id` | `VARCHAR(64)` | Nullable; indexed |
| `representative_user_id` | `INTEGER` | Foreign key to `users.id`; nullable |
| `last_json` | `TEXT` | Nullable |
| `last_poll` | `DATETIME` | Nullable |
| `last_detected_change` | `DATETIME` | Nullable |
| `last_response_time` | `FLOAT` | Nullable |
| `paused` | `BOOLEAN` | Not null; default `False` |

### `users`

Defined by `User`:

| Column | Type | Constraints / notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `username` | `VARCHAR(80)` | Unique, not null |
| `ul_name` | `VARCHAR(255)` | Nullable |
| `password_hash` | `VARCHAR(255)` | Not null |
| `ul_student_id` | `VARCHAR(64)` | Nullable |
| `ul_class_id` | `VARCHAR(64)` | Nullable |
| `ul_cookie` | Encrypted text | Nullable |
| `group_id` | `INTEGER` | Foreign key to `groups.id`; nullable |
| `last_successful_poll` | `DATETIME` | Nullable |
| `last_request_result` | `VARCHAR(255)` | Nullable |
| `last_snapshot` | `TEXT` | Nullable |
| `created_at` | `DATETIME` | Not null; UTC default |
| `updated_at` | `DATETIME` | Not null; UTC default and updated automatically |
| `last_seen_at` | `DATETIME` | Nullable |
| `is_admin` | `BOOLEAN` | Not null; default `False` |
| `hostage_consent` | `BOOLEAN` | Not null; default `False` |
| `force_password_change` | `BOOLEAN` | Not null; default `False` |

### `ul_credentials`

Defined by `ULCredential`:

| Column | Type | Constraints / notes |
|---|---|---|
| `user_id` | `INTEGER` | Primary key and foreign key to `users.id` |
| `username` | `VARCHAR(80)` | Not null |
| `password` | Encrypted text | Not null |

### `encryption_sentinel`

Defined by `EncryptionSentinel`:

| Column | Type | Constraints / notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `value` | `TEXT` | Not null |

### `grades`

Defined by `Grade`:

| Column | Type | Constraints / notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `user_id` | `INTEGER` | Foreign key to `users.id`; not null; indexed |
| `material_id` | `VARCHAR(64)` | Not null |
| `partial` | `FLOAT` | Nullable |
| `partial_rank` | `INTEGER` | Nullable |
| `final` | `FLOAT` | Nullable |
| `final_grade` | `FLOAT` | Nullable |
| `final_rank` | `INTEGER` | Nullable |
| `updated_at` | `DATETIME` | Not null; UTC default |

Additional constraint:

```sql
UNIQUE (user_id, material_id)
```

Constraint name: `uq_grades_user_material`.

### `grade_averages`

Defined by `GradeAverage`:

| Column | Type | Constraints / notes |
|---|---|---|
| `user_id` | `INTEGER` | Primary key and foreign key to `users.id` |
| `partial_average` | `FLOAT` | Nullable |
| `partial_rank` | `INTEGER` | Nullable |
| `final_average` | `FLOAT` | Nullable |
| `final_rank` | `INTEGER` | Nullable |
| `updated_at` | `DATETIME` | Not null; UTC default |

### `public_shares`

Defined by `PublicShare`:

| Column | Type | Constraints / notes |
|---|---|---|
| `user_id` | `INTEGER` | Part of composite primary key; foreign key to `users.id` |
| `item_key` | `VARCHAR(120)` | Part of composite primary key |

The primary key is composite:

```sql
PRIMARY KEY (user_id, item_key)
```

### `materials`

Defined by `Material`:

| Column | Type | Constraints / notes |
|---|---|---|
| `material_id` | `VARCHAR(64)` | Primary key |
| `code` | `VARCHAR(64)` | Not null |
| `name` | `VARCHAR(255)` | Not null |
| `credits` | `INTEGER` | Nullable |
| `image` | `VARCHAR(255)` | Nullable |

### `settings`

Defined by `Setting`:

| Column | Type | Constraints / notes |
|---|---|---|
| `key` | `VARCHAR(120)` | Primary key |
| `value` | `TEXT` | Not null |

### `logs`

Defined by `LogEntry`:

| Column | Type | Constraints / notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `level` | `VARCHAR(20)` | Not null |
| `event_type` | `VARCHAR(80)` | Not null |
| `message` | `TEXT` | Not null |
| `created_at` | `DATETIME` | Not null; UTC default |
| `user_id` | `INTEGER` | Foreign key to `users.id`; nullable |
| `group_id` | `INTEGER` | Foreign key to `groups.id`; nullable |
| `endpoint` | `VARCHAR(255)` | Nullable |
| `http_status` | `INTEGER` | Nullable |
| `response_time` | `FLOAT` | Nullable |
| `polling_duration` | `FLOAT` | Nullable |
| `exception` | `TEXT` | Nullable |

### `failed_logins`

Defined by `FailedLogin`:

| Column | Type | Constraints / notes |
|---|---|---|
| `id` | `INTEGER` | Primary key |
| `username` | `VARCHAR(80)` | Nullable; indexed |
| `ip_address` | `VARCHAR(45)` | Not null |
| `created_at` | `DATETIME` | Not null; UTC default |

## 3. Automatic schema creation

The main schema creation statement is:

```python
with app.app_context():
    db.create_all()
```

This creates missing tables from the SQLAlchemy model definitions, including columns, primary keys, foreign keys, indexes, unique constraints, and composite keys.

`db.create_all()` does not automatically migrate or alter existing tables when model definitions change.

## 4. Manual schema migrations

The function `ensure_user_name_column()` checks existing SQLite tables and adds columns that may be missing from older database versions.

Schema inspection statements:

```python
text("PRAGMA table_info(users)")
text("PRAGMA table_info(groups)")
text("PRAGMA table_info(classes)")
```

These statements only inspect the schema.

The statements that modify the schema are:

```sql
ALTER TABLE users ADD COLUMN ul_name VARCHAR(255);
ALTER TABLE users ADD COLUMN hostage_consent BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN force_password_change BOOLEAN NOT NULL DEFAULT 0;
ALTER TABLE groups ADD COLUMN class_id VARCHAR(64);
ALTER TABLE classes ADD COLUMN language VARCHAR(16);
```

They are executed conditionally only when the relevant column does not already exist.

## 5. Startup order

At application startup, the following block runs:

```python
with app.app_context():
    db.create_all()
    ensure_user_name_column()
    bootstrap_materials()
    ensure_admin_account()
    _encrypt_legacy_data()
    _verify_or_seal(app, db)
    backfill_stored_grades()
    backfill_class_names()
    start_background_services()
```

The schema-related operations are:

1. `db.create_all()` creates missing tables from the ORM models.
2. `ensure_user_name_column()` applies the manual `ALTER TABLE` migrations.
3. `bootstrap_materials()` inserts initial material records; it does not create the table.
4. `_encrypt_legacy_data()` updates existing encrypted values; it does not change the schema.
5. `_verify_or_seal(app, db)` operates on encryption-related database data; its implementation is in `crypto_utils.py`.
6. `backfill_stored_grades()` inserts migrated data into existing tables.
7. `backfill_class_names()` inserts or updates class records and groups.

## 6. Foreign-key relationships

The foreign keys declared in `app.py` are:

```text
groups.representative_user_id -> users.id
users.group_id                -> groups.id
ul_credentials.user_id       -> users.id
grades.user_id               -> users.id
grade_averages.user_id       -> users.id
public_shares.user_id        -> users.id
logs.user_id                 -> users.id
logs.group_id                -> groups.id
```

## 7. Indexes and uniqueness

The following declarations create indexes or uniqueness rules:

```python
name = db.Column(db.String(120), unique=True, nullable=False)
```

Unique `groups.name`.

```python
class_id = db.Column(db.String(64), nullable=True, index=True)
```

Index on `groups.class_id`.

```python
user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
```

Index on `grades.user_id`.

```python
username = db.Column(db.String(80), nullable=True, index=True)
```

Index on `failed_logins.username`.

```python
__table_args__ = (
    db.UniqueConstraint("user_id", "material_id", name="uq_grades_user_material"),
)
```

Unique `(user_id, material_id)` pairs in `grades`.

## 8. Important distinction

These declarations define the ORM relationships but do not independently create additional columns:

```python
relationship("User", ...)
relationship("Group", ...)
relationship("Grade", ...)
relationship("GradeAverage", ...)
relationship("PublicShare", ...)
```

The actual columns and foreign keys are created by `db.Column(...)` and `db.ForeignKey(...)` declarations.

## 9. How to export this document as PDF

Open this Markdown file on GitHub, copy it into a Markdown editor, or open it in VS Code and use **Markdown: Open Preview**, then choose **Print** and select **Save as PDF**.

You can also use a Markdown-to-PDF tool such as `pandoc`:

```bash
pandoc database-schema.md -o database-schema.pdf
```
