import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg = get_package_share_directory("robot_brain_pkg")
    config = os.path.join(pkg, "config", "brain_params.yaml")

    provider = LaunchConfiguration("provider", default="ollama")
    use_vision = LaunchConfiguration("use_vision", default="false")

    return LaunchDescription([
        DeclareLaunchArgument(
            "provider",
            default_value="ollama",
            description="LLM provider: openai | llamacpp | gemini | ollama",
        ),
        DeclareLaunchArgument(
            "use_vision",
            default_value="false",
            description="Attach latest camera frame to every query (VLM mode)",
        ),
        Node(
            package="robot_brain_pkg",
            executable="brain_node",
            name="brain_node",
            output="screen",
            parameters=[config, {"provider": provider, "use_vision": use_vision}],
        ),
    ])
