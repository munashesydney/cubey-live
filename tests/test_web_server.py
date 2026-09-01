"""
Unit tests for FastAPI Web Server & Auth endpoints.
"""

import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from src.config import config
from src.web.app import app


class WebServerApiTests(unittest.TestCase):
    """Test REST API routes, Basic Auth, and remote control endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.auth = (config.web_username or "admin", config.web_password or "cubey")

    def test_unauthorized_access_fails(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 401)

        res_bad = self.client.get("/api/status", auth=("wrong", "password"))
        self.assertEqual(res_bad.status_code, 401)

    def test_authorized_status_endpoint(self):
        res = self.client.get("/api/status", auth=self.auth)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "online")
        self.assertIn("battery", data)
        self.assertIn("lidar", data)
        self.assertIn("mapping", data)

    def test_maps_list_and_save(self):
        res = self.client.get("/api/maps", auth=self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.json(), list)

        save_res = self.client.post(
            "/api/maps", json={"name": "Web Test Map"}, auth=self.auth
        )
        self.assertEqual(save_res.status_code, 200)
        self.assertEqual(save_res.json().get("status"), "saved")

    def test_mapping_lifecycle_endpoints(self):
        nav_service = MagicMock()
        nav_service.start_manual_mapping.return_value = True
        with patch("src.web.routers.api_navigation.get_nav_service", return_value=nav_service):
            res_start = self.client.post("/api/mapping/start", auth=self.auth)
            self.assertEqual(res_start.status_code, 200)

        res_pause = self.client.post("/api/mapping/pause", auth=self.auth)
        self.assertEqual(res_pause.status_code, 200)

        res_reset = self.client.post("/api/mapping/reset", auth=self.auth)
        self.assertEqual(res_reset.status_code, 200)

    def test_autonomous_mapping_returns_503_when_nav2_is_down(self):
        nav_service = MagicMock()
        nav_service.start_exploration.return_value = False
        with patch("src.web.routers.api_navigation.get_nav_service", return_value=nav_service):
            response = self.client.post(
                "/api/mapping/start",
                json={"mode": "autonomous"},
                auth=self.auth,
            )
        self.assertEqual(response.status_code, 503)

    def test_drive_control_endpoint(self):
        res = self.client.post(
            "/api/control/move",
            json={"action": "forward", "speed": 150, "duration_ms": 100},
            auth=self.auth,
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("action"), "forward")

        stop_res = self.client.post("/api/control/stop", auth=self.auth)
        self.assertEqual(stop_res.status_code, 200)


if __name__ == "__main__":
    unittest.main()
