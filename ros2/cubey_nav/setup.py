from glob import glob
from setuptools import find_packages, setup


package_name = "cubey_nav"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/urdf", glob("urdf/*")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Cubey",
    maintainer_email="cubey@localhost",
    description="Cubey ROS 2 hardware boundary and Nav2 configuration.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "command_odometry = cubey_nav.command_odometry:main",
            "frontier_explorer = cubey_nav.frontier_explorer:main",
            "pose_relay = cubey_nav.pose_relay:main",
        ],
    },
)
