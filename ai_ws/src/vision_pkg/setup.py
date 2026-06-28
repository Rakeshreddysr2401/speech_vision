from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'vision_pkg'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('lib', package_name), glob('scripts/*')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            # detector_node removed — YOLOv8 runs as isaac_ros_yolov8 in Container 1
            'camera_node = vision_pkg.camera_node:main',
            'moondream_node = vision_pkg.moondream_node:main',
        ],
    },
)
