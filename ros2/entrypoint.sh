#!/bin/bash
set -e

source "/opt/ros/${ROS_DISTRO}/setup.bash"
source /opt/cubey_ws/install/setup.bash

# colcon's isolated ament_python layout installs each package under its own
# prefix. Some ros-base images do not emit an AMENT_PREFIX_PATH hook for that
# isolated Python prefix, even though the Python and executable hooks exist.
# Add it explicitly so `ros2 launch` can resolve cubey_nav's share directory.
export AMENT_PREFIX_PATH="/opt/cubey_ws/install/cubey_nav:${AMENT_PREFIX_PATH}"

exec "$@"
