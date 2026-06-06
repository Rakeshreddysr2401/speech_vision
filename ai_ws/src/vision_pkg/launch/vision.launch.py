from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory('vision_pkg'), 'config', 'vision_params.yaml'
    )

    return LaunchDescription([
        Node(
            package='vision_pkg',
            executable='detector_node',
            name='detector_node',
            parameters=[config],
            output='screen',
        ),
        Node(
            package='vision_pkg',
            executable='moondream_node',
            name='moondream_node',
            parameters=[config],
            output='screen',
        ),
    ])
