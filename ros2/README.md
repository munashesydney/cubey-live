# Cubey ROS 2/Nav2 runtime

This directory contains the isolated ROS 2 Jazzy runtime for Cubey. The
Raspberry Pi host remains Debian and continues to run the existing GUI, web
server, audio stack, ESP32 UART transport, and C1 packet decoder. The container
owns localization, SLAM, costmaps, planning, recovery behaviors, and autonomous
velocity generation.

The migration is deliberately staged:

1. `bridge.launch.py` starts rosbridge, the robot model, and conservative
   command-derived odometry. It never produces autonomous motor commands.
2. The host publishes `LaserScan` and executed-motion estimates and consumes
   velocity commands only when the ROS backend is explicitly commissioned.
3. `navigation.launch.py` (the Compose default) adds SLAM Toolbox and Nav2.
   Keep `ROS2_COMMAND_OUTPUT_ENABLED=false` for monitor-only commissioning;
   the full ROS graph runs, but no ROS velocity command reaches the motors.

The command-derived odometry is an interim compatibility layer for the current
hardware. Wheel encoders and an IMU remain the recommended replacement.

## Pi commands

```bash
cd ~/Desktop/cubey-live/ros2
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
sudo docker compose logs --tail=100
```

The bridge listens only on loopback at `ws://127.0.0.1:9090`.
