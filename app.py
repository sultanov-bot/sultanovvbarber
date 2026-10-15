import os
import re
import sqlite3
import threading
import urllib.parse
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request
from psycopg import Error as PostgresError
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from werkzeug.exceptions import HTTPException

app = Flask(__name__)

WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "996700656524")
SERVICES = {
    "Стрижка - 800 сом",
    "Моделирование бороды - 700 сом",
    "Детская стрижка - 600 сом",
    "Стрижка + борода - 1300 сом",
    "Коррекция бороды - 500 сом",
    "Чистка лица - 800 сом",
}
TIMES = tuple(
    (datetime.combine(date.min, time(10, 0)) + timedelta(minutes=30 * index))
    .time()
    .strftime("%H:%M")
    for index in range(23)
)

_database_lock = threading.Lock()


def _database_url() -> str:
    configured_url = app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    if configured_url:
        return configured_url
    if os.environ.get("RENDER_SERVICE_ID"):
        raise RuntimeError("DATABASE_URL must be configured for the Render service.")

    database_path = Path(app.instance_path) / "bookings.sqlite3"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{database_path.as_posix()}"


def _connect():
    database_url = _database_url()
    if database_url.startswith(("postgres://", "postgresql://")):
        import psycopg

        return psycopg.connect(
            database_url.replace("postgres://", "postgresql://", 1),
            row_factory=dict_row,
        )

    if database_url.startswith("sqlite:///"):
        database_path = Path(database_url.removeprefix("sqlite:///"))
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    raise RuntimeError("DATABASE_URL must use PostgreSQL or SQLite.")


def _initialize_database() -> None:
    initialized_databases = app.extensions.setdefault("initialized_databases", set())
    database_url = _database_url()
    if database_url in initialized_databases:
        return

    with _database_lock:
        if database_url in initialized_databases:
            return

        connection = _connect()
        try:
            if database_url.startswith(("postgres://", "postgresql://")):
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bookings (
                        visit_date DATE NOT NULL,
                        visit_time TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (visit_date, visit_time)
                    )
                    """
                )
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bookings (
                        visit_date TEXT NOT NULL,
                        visit_time TEXT NOT NULL,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (visit_date, visit_time)
                    )
                    """
                )
            connection.commit()
        finally:
            connection.close()
        initialized_databases.add(database_url)


@app.before_request
def ensure_database() -> None:
    if request.endpoint in {"index", "get_booked", "book"}:
        try:
            _initialize_database()
        except (OSError, RuntimeError, sqlite3.Error, PostgresError):
            app.logger.exception("Could not initialize the booking database.")
            abort(503, description="Сервис записи временно недоступен. Попробуйте позже.")


def _parse_date(value: str | None) -> date:
    if not value:
        abort(400, description="Выберите дату визита.")
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        abort(400, description="Укажите дату в правильном формате.")
    if parsed_date.isoformat() != value:
        abort(400, description="Укажите дату в правильном формате.")
    return parsed_date


def _bishkek_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=6)))


def _normalize_phone(value: str | None) -> str:
    phone = re.sub(r"[\s().-]", "", value or "")
    if phone.startswith("00"):
        phone = f"+{phone[2:]}"
    elif phone.startswith("996"):
        phone = f"+{phone}"
    elif phone.startswith("0") and len(phone) == 10:
        phone = f"+996{phone[1:]}"
    elif len(phone) == 9 and phone.isdigit():
        phone = f"+996{phone}"
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        abort(400, description="Введите номер телефона с кодом страны, например +996 700 123 456.")
    return phone


@app.errorhandler(HTTPException)
def handle_http_error(error: HTTPException):
    status_code = error.code or 500
    message = error.description
    if request.path == "/book":
        return render_template(
            "booking_error.html",
            message=message,
            status_code=status_code,
        ), status_code
    if request.path == "/get_booked":
        return jsonify(error=message), status_code
    return error


@app.route("/")
def index():
    return render_template("index.html", times=TIMES)


@app.route("/get_booked", methods=["GET"])
def get_booked():
    visit_date = _parse_date(request.args.get("date"))
    if visit_date < _bishkek_now().date():
        return jsonify(date=visit_date.isoformat(), booked=[])

    connection = None
    try:
        connection = _connect()
        if _database_url().startswith(("postgres://", "postgresql://")):
            rows = connection.execute(
                "SELECT visit_time FROM bookings WHERE visit_date = %s ORDER BY visit_time",
                (visit_date,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT visit_time FROM bookings WHERE visit_date = ? ORDER BY visit_time",
                (visit_date.isoformat(),),
            ).fetchall()
        booked = [row["visit_time"] for row in rows]
    except (sqlite3.Error, PostgresError):
        app.logger.exception("Could not retrieve booked time slots.")
        abort(503, description="Не удалось загрузить свободное время. Обновите страницу.")
    finally:
        if connection is not None:
            connection.close()

    return jsonify(date=visit_date.isoformat(), booked=booked)


@app.route("/book", methods=["POST"])
def book():
    name = (request.form.get("name") or "").strip()
    phone = _normalize_phone(request.form.get("phone"))
    service = request.form.get("service", "")
    visit_date = _parse_date(request.form.get("date"))
    visit_time = request.form.get("time", "")

    if not name or len(name) > 80:
        abort(400, description="Введите имя (не более 80 символов).")
    if service not in SERVICES:
        abort(400, description="Выберите услугу из списка.")
    if visit_time not in TIMES:
        abort(400, description="Выберите время из списка.")

    visit_datetime = datetime.combine(
        visit_date,
        time.fromisoformat(visit_time),
        tzinfo=timezone(timedelta(hours=6)),
    )
    if visit_datetime <= _bishkek_now():
        abort(400, description="Нельзя записаться на прошедшую дату или время.")

    connection = None
    try:
        connection = _connect()
        if _database_url().startswith(("postgres://", "postgresql://")):
            connection.execute(
                "INSERT INTO bookings (visit_date, visit_time) VALUES (%s, %s)",
                (visit_date, visit_time),
            )
        else:
            connection.execute(
                "INSERT INTO bookings (visit_date, visit_time) VALUES (?, ?)",
                (visit_date.isoformat(), visit_time),
            )
        connection.commit()
    except (sqlite3.IntegrityError, UniqueViolation):
        if connection is not None:
            connection.rollback()
        abort(409, description="Это время уже занято. Выберите другой слот.")
    except (sqlite3.Error, PostgresError):
        if connection is not None:
            connection.rollback()
        app.logger.exception("Could not save the booking.")
        abort(503, description="Не удалось сохранить запись. Попробуйте позже.")
    finally:
        if connection is not None:
            connection.close()

    whatsapp_text = (
        "👑 *ОНЛАЙН-ЗАПИСЬ В SULTANOV BARBER* 👑\n\n"
        f"👤 *Имя:* {name}\n"
        f"📞 *Телефон клиента для связи:* {phone}\n"
        f"✂️ *Услуга:* {service}\n"
        f"📅 *Дата:* {visit_date.strftime('%d.%m.%Y')}\n"
        f"⏰ *Время:* {visit_time}\n\n"
        "Клиент выбрал время на сайте. Ожидает подтверждения."
    )
    whatsapp_url = (
        f"https://wa.me/{WHATSAPP_NUMBER}?"
        f"{urllib.parse.urlencode({'text': whatsapp_text})}"
    )
    return redirect(whatsapp_url)


if __name__ == "__main__":
    app.run(debug=False)
