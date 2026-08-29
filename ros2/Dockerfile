FROM ros:jazzy-ros-base

ENV DEBIAN_FRONTEND=noninteractive
ENV ROS_DISTRO=jazzy

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ros-jazzy-navigation2 \
        ros-jazzy-nav2-bringup \
        ros-jazzy-slam-toolbox \
        ros-jazzy-rosbridge-suite \
        ros-jazzy-robot-state-publisher \
        ros-jazzy-tf2-tools \
        ros-jazzy-xacro \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/cubey_ws
COPY cubey_nav /opt/cubey_ws/src/cubey_nav

RUN . /opt/ros/${ROS_DISTRO}/setup.sh \
    && colcon build

COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "cubey_nav", "bridge.launch.py"]
