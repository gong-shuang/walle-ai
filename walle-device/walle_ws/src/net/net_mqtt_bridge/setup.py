import os
from glob import glob

from setuptools import setup

package_name = "net_mqtt_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages",
         ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"),
         glob("launch/*.launch.py")),
        (os.path.join("share", package_name, "config"),
         glob("config/*.yaml")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="gs",
    maintainer_email="gs@example.com",
    description="MQTT protocol bridge: MQTT topics to ROS2 services and status",
    license="Proprietary",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "net_mqtt_bridge = net_mqtt_bridge.node:main",
        ],
    },
)
