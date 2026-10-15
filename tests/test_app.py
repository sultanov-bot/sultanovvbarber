import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import _bishkek_now, app


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "bookings.sqlite3"
        app.config["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
        app.extensions["initialized_databases"] = set()
        app.config["TESTING"] = True
        self.client = app.test_client()
        self.visit_date = (_bishkek_now().date() + timedelta(days=1)).isoformat()

    def tearDown(self):
        self.temp_dir.cleanup()
        app.config.pop("DATABASE_URL", None)
        app.config["TESTING"] = False

    def booking_data(self):
        return {
            "name": "Азамат",
            "phone": "0700 123 456",
            "service": "Стрижка - 800 сом",
            "date": self.visit_date,
            "time": "12:00",
        }

    def test_booking_is_saved_and_time_is_blocked(self):
        response = self.client.post("/book", data=self.booking_data())

        self.assertEqual(response.status_code, 302)
        target = urlparse(response.headers["Location"])
        self.assertEqual(target.netloc, "wa.me")
        self.assertEqual(target.path, "/996700656524")
        message = parse_qs(target.query)["text"][0]
        self.assertIn("+996700123456", message)
        expected_date = (_bishkek_now().date() + timedelta(days=1)).strftime("%d.%m.%Y")
        self.assertIn(expected_date, message)

        availability = self.client.get(f"/get_booked?date={self.visit_date}")
        self.assertEqual(availability.status_code, 200)
        self.assertEqual(availability.json["booked"], ["12:00"])

        duplicate = self.client.post("/book", data=self.booking_data())
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn("уже занято", duplicate.get_data(as_text=True))

    def test_past_dates_cannot_be_booked(self):
        data = self.booking_data()
        data["date"] = (_bishkek_now().date() - timedelta(days=1)).isoformat()

        response = self.client.post("/book", data=data)

        self.assertEqual(response.status_code, 400)
        self.assertIn("прошедшую дату", response.get_data(as_text=True))

    def test_booking_rejects_unlisted_service_and_time(self):
        invalid_service = self.booking_data()
        invalid_service["service"] = "Несуществующая услуга"
        invalid_time = self.booking_data()
        invalid_time["time"] = "22:00"

        self.assertEqual(self.client.post("/book", data=invalid_service).status_code, 400)
        self.assertEqual(self.client.post("/book", data=invalid_time).status_code, 400)

    def test_homepage_renders_mobile_booking_controls(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="time-slots"', response.data)
        self.assertIn(b'Asia/Bishkek', response.data)
        self.assertIn(b'is-occupied', response.data)
        self.assertIn(b'#421b20', response.data)


if __name__ == "__main__":
    unittest.main()
