from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("robot_voice_pkg")
    config = os.path.join(pkg, "config", "voice_params.yaml")

    tts_engine = LaunchConfiguration("tts_engine", default="kokoro")

    return LaunchDescription([
        DeclareLaunchArgument(
            "tts_engine",
            default_value="kokoro",
            description="TTS backend to use: 'kokoro' or 'piper'",
        ),
        Node(
            package="robot_voice_pkg",
            executable="stt_node",
            name="stt_node",
            output="screen",
            parameters=[config],
        ),
        Node(
            package="robot_voice_pkg",
            executable="tts_node",
            name="tts_node",
            output="screen",
            parameters=[config, {"engine": tts_engine}],
        ),
    ])
