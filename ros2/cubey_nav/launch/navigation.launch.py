from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    cubey_share = get_package_share_directory("cubey_nav")
    slam_share = get_package_share_directory("slam_toolbox")
    nav_params = f"{cubey_share}/config/nav2.yaml"
    slam_params = f"{cubey_share}/config/slam_toolbox.yaml"
    tf_remaps = [("/tf", "tf"), ("/tf_static", "tf_static")]
    lifecycle_nodes = [
        "controller_server",
        "smoother_server",
        "planner_server",
        "behavior_server",
        "velocity_smoother",
        "collision_monitor",
        "bt_navigator",
    ]

    nav_nodes = [
        Node(
            package="nav2_controller",
            executable="controller_server",
            name="controller_server",
            parameters=[nav_params],
            remappings=tf_remaps + [("cmd_vel", "cmd_vel_nav")],
            output="screen",
        ),
        Node(
            package="nav2_smoother",
            executable="smoother_server",
            name="smoother_server",
            parameters=[nav_params],
            remappings=tf_remaps,
            output="screen",
        ),
        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            parameters=[nav_params],
            remappings=tf_remaps,
            output="screen",
        ),
        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            parameters=[nav_params],
            remappings=tf_remaps + [("cmd_vel", "cmd_vel_nav")],
            output="screen",
        ),
        Node(
            package="nav2_bt_navigator",
            executable="bt_navigator",
            name="bt_navigator",
            parameters=[nav_params],
            remappings=tf_remaps,
            output="screen",
        ),
        Node(
            package="nav2_velocity_smoother",
            executable="velocity_smoother",
            name="velocity_smoother",
            parameters=[nav_params],
            remappings=tf_remaps + [("cmd_vel", "cmd_vel_nav")],
            output="screen",
        ),
        Node(
            package="nav2_collision_monitor",
            executable="collision_monitor",
            name="collision_monitor",
            parameters=[nav_params],
            remappings=tf_remaps,
            output="screen",
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            parameters=[
                {"autostart": True, "bond_timeout": 4.0},
                {"node_names": lifecycle_nodes},
            ],
            output="screen",
        ),
    ]

    return LaunchDescription(
        [
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    f"{cubey_share}/launch/bridge.launch.py"
                )
            ),
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    f"{slam_share}/launch/online_async_launch.py"
                ),
                launch_arguments={
                    "slam_params_file": slam_params,
                    "use_sim_time": "false",
                    "autostart": "true",
                }.items(),
            ),
            Node(
                package="cubey_nav",
                executable="frontier_explorer",
                name="frontier_explorer",
                parameters=[{"enabled_on_start": False}],
                output="screen",
            ),
            Node(
                package="cubey_nav",
                executable="pose_relay",
                name="pose_relay",
                output="screen",
            ),
            *nav_nodes,
        ]
    )
