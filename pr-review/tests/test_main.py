import os
import unittest

from fastapi.testclient import TestClient

from app.main import app


class AuthMiddlewareTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.original_password = os.environ.get("ACCESS_PASSWORD")
        self.original_secret = os.environ.get("ACCESS_SESSION_SECRET")
        os.environ["ACCESS_PASSWORD"] = "secret123"
        os.environ["ACCESS_SESSION_SECRET"] = "test-secret"

    def tearDown(self):
        if self.original_password is None:
            os.environ.pop("ACCESS_PASSWORD", None)
        else:
            os.environ["ACCESS_PASSWORD"] = self.original_password

        if self.original_secret is None:
            os.environ.pop("ACCESS_SESSION_SECRET", None)
        else:
            os.environ["ACCESS_SESSION_SECRET"] = self.original_secret

    def test_health_endpoint_stays_public(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_password_gate_redirects_page_requests(self):
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        self.assertIn("访问验证", login_page.text)

    def test_password_gate_blocks_api_until_logged_in(self):
        blocked = self.client.post("/api/review", json={})
        self.assertEqual(blocked.status_code, 401)

        wrong_password = self.client.post("/api/login", json={"password": "wrong"})
        self.assertEqual(wrong_password.status_code, 401)

        logged_in = self.client.post("/api/login", json={"password": "secret123"})
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.json()["status"], "ok")

        home = self.client.get("/", follow_redirects=False)
        self.assertEqual(home.status_code, 200)


if __name__ == "__main__":
    unittest.main()
