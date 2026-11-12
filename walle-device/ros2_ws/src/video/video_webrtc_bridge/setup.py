import os
from glob import glob

from setuptools import setup

package_name = "video_webrtc_bridge"

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
    description="Bridge /image_raw to an RTMP/WebRTC streaming server",
    license="Proprietary",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "video_webrtc_bridge = video_webrtc_bridge.node:main",
            "walle_camera = video_webrtc_bridge.camera_shm_node:main",
        ],
    },
)
