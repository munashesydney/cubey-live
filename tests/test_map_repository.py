"""
Unit tests for MapRepository SQLite operations.
"""

import unittest
import zlib

from src.db.models.map import MapModel
from src.db.repositories.map_repository import (
    create_map,
    delete_map,
    get_active_map,
    get_map,
    list_maps,
    set_active_map,
    update_map,
)


class MapRepositoryTests(unittest.TestCase):
    """Test map persistence and querying in SQLite."""

    def test_map_crud_lifecycle(self):
        fake_grid = zlib.compress(b"\x00" * 400)

        # Create map
        map_obj = create_map(
            name="Test Living Room",
            grid_data=fake_grid,
            width=20,
            height=20,
            resolution_cm=5.0,
            is_active=True,
        )

        self.assertIsNotNone(map_obj.id)
        self.assertEqual(map_obj.name, "Test Living Room")
        self.assertTrue(map_obj.is_active)

        # Get map
        fetched = get_map(map_obj.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Test Living Room")

        # Get active map
        active = get_active_map()
        self.assertIsNotNone(active)
        self.assertEqual(active.id, map_obj.id)

        # Update map
        updated = update_map(map_obj.id, name="Renamed Living Room")
        self.assertEqual(updated.name, "Renamed Living Room")

        # List maps
        all_maps = list_maps()
        self.assertGreaterEqual(len(all_maps), 1)

        # Delete map
        del_result = delete_map(map_obj.id)
        self.assertTrue(del_result)
        self.assertIsNone(get_map(map_obj.id))


if __name__ == "__main__":
    unittest.main()
