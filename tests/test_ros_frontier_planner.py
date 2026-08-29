from ros2.cubey_nav.cubey_nav.frontier_planner import (
    GridMap,
    select_frontier_goal,
)


def _grid_with_known_room() -> GridMap:
    width = 32
    height = 32
    data = [-1] * (width * height)
    for y in range(5, 27):
        for x in range(5, 27):
            data[y * width + x] = 0
    return GridMap(
        width=width,
        height=height,
        resolution=0.05,
        origin_x=-0.8,
        origin_y=-0.8,
        data=data,
    )


def test_selects_known_clear_standoff_goal() -> None:
    grid = _grid_with_known_room()

    goal = select_frontier_goal(
        grid,
        robot_x=0.0,
        robot_y=0.0,
        minimum_cluster_cells=6,
        clearance_m=0.15,
        standoff_m=0.20,
    )

    assert goal is not None
    assert grid.value(goal.cell_x, goal.cell_y) == 0
    assert (goal.x**2 + goal.y**2) ** 0.5 > 0.15


def test_excluded_goal_is_not_selected_again() -> None:
    grid = _grid_with_known_room()
    first = select_frontier_goal(
        grid,
        robot_x=0.0,
        robot_y=0.0,
        minimum_cluster_cells=6,
        clearance_m=0.15,
        standoff_m=0.20,
    )
    assert first is not None

    second = select_frontier_goal(
        grid,
        robot_x=0.0,
        robot_y=0.0,
        excluded_world=[(first.x, first.y)],
        excluded_radius_m=2.0,
        minimum_cluster_cells=6,
        clearance_m=0.15,
        standoff_m=0.20,
    )

    assert second is None


def test_returns_none_without_unknown_boundary() -> None:
    grid = GridMap(
        width=12,
        height=12,
        resolution=0.05,
        origin_x=0.0,
        origin_y=0.0,
        data=[0] * 144,
    )

    assert select_frontier_goal(grid, 0.3, 0.3) is None
