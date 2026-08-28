"""
Unit tests for LidarPage GUI component: layout instantiation, scan rendering dispatch,
zoom settings, and palette changes.
"""

import unittest
import customtkinter as ctk

from src.gui.pages.lidar_page import LidarPage
from src.services.lidar_service import LidarPoint, LidarScanData, LidarService


class LidarGuiTests(unittest.TestCase):
    """Test LidarPage widget creation and UI update callbacks."""

    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        self.service = LidarService(default_port="MOCK_SIMULATOR")
        self.page = LidarPage(self.root, lidar_service=self.service)
        self.page.pack(fill="both", expand=True)
        self.root.update_idletasks()

    def tearDown(self):
        try:
            self.service.disconnect()
            self.page.destroy()
            self.root.destroy()
        except Exception:
            pass

    def test_page_widget_structure(self):
        self.assertIsNotNone(self.page.radar_canvas)
        self.assertIsNotNone(self.page.port_combo)
        self.assertIsNotNone(self.page.baud_combo)
        self.assertIsNotNone(self.page.btn_connect)
        self.assertIsNotNone(self.page.btn_scan)
        self.assertIsNotNone(self.page.lbl_dist_front)

    def test_palette_and_safety_changes(self):
        self.page._on_palette_changed("Safety")
        self.assertEqual(self.page._color_mode, "Safety")

        self.page._on_safety_slider_changed(450)
        self.assertEqual(self.page._safety_dist_mm, 450)

        self.page._set_range_preset(6.0)
        self.assertEqual(self.page._max_range_m, 6.0)

    def test_scan_data_ui_update(self):
        pts = [
            LidarPoint(angle_deg=0.0, distance_mm=250.0, quality=60),
            LidarPoint(angle_deg=90.0, distance_mm=1200.0, quality=60),
        ]
        scan = LidarScanData(
            points=pts,
            scan_rate_hz=10.2,
            sample_rate_hz=4800.0,
            point_count=2,
            min_front_dist_mm=250,
            min_right_dist_mm=1200,
            closest_point=pts[0],
            health_status="OK",
        )

        self.page._on_scan_data_received(scan)
        self.root.update()

        self.assertIn("250", self.page.lbl_dist_front.cget("text"))
        self.assertIn("10.2", self.page.lbl_fps.cget("text"))


if __name__ == "__main__":
    unittest.main()
