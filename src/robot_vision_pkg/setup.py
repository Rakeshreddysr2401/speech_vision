from setuptools import setup, find_packages
from glob import glob

package_name = "robot_vision_pkg"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/config", glob("config/*.yaml")),
        (f"share/{package_name}/launch", glob("launch/*.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    entry_points={
        "console_scripts": [
            f"camera_node    = {package_name}.camera_node:main",
            f"detector_node  = {package_name}.detector_node:main",
            f"depth_node     = {package_name}.depth_node:main",
            f"tracker_node   = {package_name}.tracker_node:main",
            f"spatial_node   = {package_name}.spatial_node:main",
            f"moondream_node = {package_name}.moondream_node:main",
            f"scene_node     = {package_name}.scene_node:main",
        ],
    },
)
