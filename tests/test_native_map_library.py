from src.services.navigation.map_library import NativeMapLibrary


def test_lists_only_native_slam_maps_and_marks_incomplete_entries(tmp_path):
    complete = tmp_path / "cubey_floorplan_20260914_120000"
    complete.with_suffix(".posegraph").write_bytes(b"pose graph")
    complete.with_suffix(".data").write_bytes(b"scan data")
    complete.with_suffix(".pgm").write_bytes(b"P5\n1 1\n255\n\x00")
    complete.with_suffix(".yaml").write_text("resolution: 0.05\n", encoding="utf-8")

    incomplete = tmp_path / "cubey_floorplan_incomplete"
    incomplete.with_suffix(".posegraph").write_bytes(b"pose graph")
    dotted = tmp_path / "cubey.floorplan.v1"
    (tmp_path / "cubey.floorplan.v1.posegraph").write_bytes(b"pose graph")
    (tmp_path / "cubey.floorplan.v1.data").write_bytes(b"scan data")
    (tmp_path / "not_a_map.txt").write_text("ignore me", encoding="utf-8")

    library = NativeMapLibrary(tmp_path)
    maps = library.list()
    by_id = {native_map.map_id: native_map for native_map in maps}

    assert set(by_id) == {
        "cubey_floorplan_20260914_120000",
        "cubey_floorplan_incomplete",
        "cubey.floorplan.v1",
    }
    assert by_id["cubey_floorplan_20260914_120000"].loadable
    assert by_id["cubey_floorplan_20260914_120000"].has_image
    assert by_id["cubey_floorplan_20260914_120000"].resolution_cm == 5.0
    assert not by_id["cubey_floorplan_incomplete"].loadable
    assert by_id["cubey.floorplan.v1"].loadable


def test_rejects_path_traversal_and_unknown_maps(tmp_path):
    library = NativeMapLibrary(tmp_path)
    assert library.get("../outside") is None
    assert library.get("with/slash") is None
    assert library.get("does-not-exist") is None
