from launch import LaunchDescription
from launch.substitutions import Command, FindExecutable, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description() -> LaunchDescription:
    robot_description = Command(
        [
            FindExecutable(name="xacro"),
            " ",
            PathJoinSubstitution(
                [FindPackageShare("cubey_nav"), "urdf", "cubey.urdf.xacro"]
            ),
        ]
    )

    return LaunchDescription(
        [
            Node(
                package="robot_state_publisher",
                executable="robot_state_publisher",
                name="robot_state_publisher",
                parameters=[{"robot_description": robot_description}],
                output="screen",
            ),
            Node(
                package="cubey_nav",
                executable="command_odometry",
                name="command_odometry",
                parameters=[
                    {
                        "command_timeout_s": 0.55,
                        "odom_frame": "odom",
                        "base_frame": "base_footprint",
                    }
                ],
                output="screen",
            ),
            Node(
                package="rosbridge_server",
                executable="rosbridge_websocket",
                name="rosbridge_websocket",
                parameters=[
                    {
                        "address": "127.0.0.1",
                        "port": 9090,
                        "authenticate": False,
                    }
                ],
                output="screen",
            ),
        ]
    )
