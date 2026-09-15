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

    def test_maps_list_uses_native_library_and_rejects_legacy_saves(self):
        library = MagicMock()
        library.list.return_value = []
        with patch("src.web.routers.api_maps.get_native_map_library", return_value=library):
            res = self.client.get("/api/maps", auth=self.auth)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), [])

        save_res = self.client.post("/api/maps", auth=self.auth)
        self.assertEqual(save_res.status_code, 409)
        self.assertIn("automatically", save_res.json().get("detail", ""))

    def test_loads_a_native_map_through_ros_navigation_service(self):
        native_map = MagicMock()
        native_map.loadable = True
        native_map.map_id = "cubey_floorplan_20260914_120000"
        native_map.display_name = "cubey floorplan 20260914 120000"
        library = MagicMock()
        library.get.return_value = native_map
        nav_service = MagicMock()
        nav_service.load_saved_map.return_value = True
        with patch("src.web.routers.api_maps.get_native_map_library", return_value=library), \
             patch("src.web.routers.api_maps.get_nav_service", return_value=nav_service):
            response = self.client.post(
                "/api/maps/cubey_floorplan_20260914_120000/load", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        nav_service.load_saved_map.assert_called_once_with("cubey_floorplan_20260914_120000")
        self.assertEqual(response.json().get("status"), "loaded")

    def test_global_localization_uses_native_map_and_navigation_service(self):
        native_map = MagicMock()
        native_map.has_image = True
        native_map.map_id = "cubey_floorplan_20260914_120000"
        native_map.display_name = "cubey floorplan 20260914 120000"
        library = MagicMock()
        library.get.return_value = native_map
        nav_service = MagicMock()
        nav_service.localize_saved_map.return_value = True
        with patch("src.web.routers.api_maps.get_native_map_library", return_value=library), \
             patch("src.web.routers.api_maps.get_nav_service", return_value=nav_service):
            response = self.client.post(
                "/api/maps/cubey_floorplan_20260914_120000/localize", auth=self.auth
            )
        self.assertEqual(response.status_code, 200)
        nav_service.localize_saved_map.assert_called_once_with(
            "cubey_floorplan_20260914_120000"
        )
        self.assertEqual(response.json().get("status"), "localized")

    def test_mapping_lifecycle_endpoints(self):
        nav_service = MagicMock()
        nav_service.start_manual_mapping.return_value = True
        nav_service.reset_mapping.return_value = True
        with patch("src.web.routers.api_navigation.get_nav_service", return_value=nav_service):
            res_start = self.client.post("/api/mapping/start", auth=self.auth)
            self.assertEqual(res_start.status_code, 200)

            res_pause = self.client.post("/api/mapping/pause", auth=self.auth)
            self.assertEqual(res_pause.status_code, 200)

            res_reset = self.client.post("/api/mapping/reset", auth=self.auth)
            self.assertEqual(res_reset.status_code, 200)
            nav_service.reset_mapping.assert_called_once_with()

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
