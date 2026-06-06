from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    vision_launch = os.path.join(
        get_package_share_directory('vision_pkg'), 'launch', 'vision.launch.py'
    )

    return LaunchDescription([
        IncludeLaunchDescription(PythonLaunchDescriptionSource(vision_launch)),
    ])
