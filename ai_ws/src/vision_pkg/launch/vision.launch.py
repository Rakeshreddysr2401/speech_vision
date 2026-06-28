from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    config = os.path.join(
        get_package_share_directory('vision_pkg'), 'config', 'vision_params.yaml'
    )

    return LaunchDescription([
        # detector_node removed — YOLOv8 runs as isaac_ros_yolov8 in Container 1 (NITROS zero-copy)
        # camera_node: single source of /camera/color/image_raw (Logitech USB cam on Jetson).
        # The D555 PoE depth camera will publish these topics natively later.
        Node(
            package='vision_pkg',
            executable='camera_node',
            name='camera_node',
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
