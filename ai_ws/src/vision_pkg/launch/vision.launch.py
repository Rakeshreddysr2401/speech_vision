from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory('vision_pkg'), 'config', 'vision_params.yaml'
    )

    return LaunchDescription([
        # camera_node: single source of /camera/color/image_raw (Logitech USB cam on Jetson).
        # Feeds the Pi5 agent's look() tool and the local target_node.
        Node(
            package='vision_pkg',
            executable='camera_node',
            name='camera_node',
            parameters=[config],
            output='screen',
        ),
        # target_node: YOLOv8n visual servoing — "where is <target>" for nav.
        # Idle until the Pi5 agent sets a target on /vision/target.
        Node(
            package='vision_pkg',
            executable='target_node',
            name='target_node',
            parameters=[config],
            output='screen',
        ),
    ])
