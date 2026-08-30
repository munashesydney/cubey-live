import os
import sys
from pathlib import Path
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    # Base directory for ros2 workspace configs
    ros2_dir = Path(__file__).resolve().parent.parent
    config_dir = ros2_dir / "config"
    nodes_dir = ros2_dir / "nodes"

    slam_params_file = str(config_dir / "slam_toolbox_params.yaml")
    nav2_params_file = str(config_dir / "nav2_params.yaml")
    cmd_bridge_script = str(nodes_dir / "cmd_vel_serial_bridge.py")
    odom_script = str(nodes_dir / "cubey_odometry_node.py")
    rplidar_script = str(nodes_dir / "rplidar_c1_node.py")

    # Launch Configurations
    use_sim_time = LaunchConfiguration("use_sim_time", default="false")
    lidar_port = LaunchConfiguration("lidar_port", default="/dev/ttyUSB0")
    lidar_baud = LaunchConfiguration("lidar_baud", default="460800")
    serial_port = LaunchConfiguration("serial_port", default="/dev/ttyAMA0")

    lifecycle_nodes = [
        "slam_toolbox",
        "controller_server",
        "planner_server",
        "behavior_server",
        "bt_navigator",
    ]

    return LaunchDescription([
        # Set unbuffered output
        SetEnvironmentVariable("RCUTILS_LOGGING_BUFFERED_STREAM", "0"),
        SetEnvironmentVariable("RCUTILS_COLORIZED_OUTPUT", "1"),

        # -----------------------------------------------------------------
        # 1. Coordinate Transforms (TF)
        # -----------------------------------------------------------------
        # base_link -> laser
        # LiDAR position: Centered left/right (Y=0.0), shifted 35mm rearward (X=-0.035m), Z=0.10m
        Node(
            package="tf2_ros",
            executable="static_transform_publisher",
            name="base_link_to_laser",
            arguments=["--x", "-0.035", "--y", "0.0", "--z", "0.10", "--yaw", "0.0", "--pitch", "0.0", "--roll", "0.0", "--frame-id", "base_link", "--child-frame-id", "laser"],
            output="screen",
        ),

        # -----------------------------------------------------------------
        # 2. RPLIDAR C1 Sensor Driver (/dev/ttyUSB0 @ 460800)
        # -----------------------------------------------------------------
        Node(
            executable=sys.executable,
            arguments=["-u", rplidar_script],
            name="rplidar_c1_node",
            parameters=[{
                "serial_port": lidar_port,
                "serial_baudrate": lidar_baud,
                "frame_id": "laser",
                "min_range": 0.05,
                "max_range": 12.0,
            }],
            output="screen",
        ),

        # -----------------------------------------------------------------
        # 3. 2D Laser & Command Odometry (Cubey Odometry Node)
        # Publishes /odom & (odom -> base_link TF)
        # -----------------------------------------------------------------
        Node(
            executable=sys.executable,
            arguments=["-u", odom_script],
            name="cubey_odometry_node",
            parameters=[{
                "odom_frame": "odom",
                "base_frame": "base_link",
                "publish_tf": True,
                "freq": 15.0,
            }],
            output="screen",
        ),

        # -----------------------------------------------------------------
        # 4. SLAM Toolbox (2D Occupancy Grid Mapping & Loop Closure)
        # -----------------------------------------------------------------
        Node(
            package="slam_toolbox",
            executable="async_slam_toolbox_node",
            name="slam_toolbox",
            parameters=[slam_params_file, {"use_sim_time": use_sim_time}],
            output="screen",
        ),

        # -----------------------------------------------------------------
        # 5. Nav2 Navigation Stack
        # -----------------------------------------------------------------
        Node(
            package="nav2_controller",
            executable="controller_server",
            name="controller_server",
            parameters=[nav2_params_file, {"use_sim_time": use_sim_time}],
            remappings=[("/cmd_vel", "/cmd_vel")],
            output="screen",
        ),
        Node(
            package="nav2_planner",
            executable="planner_server",
            name="planner_server",
            parameters=[nav2_params_file, {"use_sim_time": use_sim_time}],
            output="screen",
        ),
        Node(
            package="nav2_behaviors",
            executable="behavior_server",
            name="behavior_server",
            parameters=[nav2_params_file, {"use_sim_time": use_sim_time}],
            output="screen",
        ),
        Node(
            package="nav2_bt_navigator",
            executable="bt_navigator",
            name="bt_navigator",
            parameters=[nav2_params_file, {"use_sim_time": use_sim_time}],
            output="screen",
        ),
        Node(
            package="nav2_lifecycle_manager",
            executable="lifecycle_manager",
            name="lifecycle_manager_navigation",
            parameters=[{
                "use_sim_time": use_sim_time,
                "autostart": True,
                "bond_timeout": 0.0,
                "node_names": lifecycle_nodes,
            }],
            output="screen",
        ),


        # -----------------------------------------------------------------
        # 6. ESP32 Serial cmd_vel Bridge (/dev/ttyAMA0)
        # -----------------------------------------------------------------
        Node(
            executable=sys.executable,
            arguments=["-u", cmd_bridge_script],
            name="cubey_cmd_vel_bridge",
            parameters=[{
                "serial_port": serial_port,
                "baudrate": 115200,
            }],
            output="screen",
        ),

    ])

