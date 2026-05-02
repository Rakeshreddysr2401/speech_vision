from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import os
from ament_index_python.packages import get_package_share_directory


def generate_launch_description():
    pkg = get_package_share_directory("robot_vision_pkg")
    config = os.path.join(pkg, "config", "vision_params.yaml")

    return LaunchDescription([
        DeclareLaunchArgument("enable_detector", default_value="true"),
        DeclareLaunchArgument("enable_depth", default_value="true"),
        DeclareLaunchArgument("enable_tracker", default_value="true"),
        DeclareLaunchArgument("enable_spatial", default_value="true"),
        DeclareLaunchArgument("enable_moondream", default_value="true"),
        DeclareLaunchArgument("enable_scene", default_value="true"),

        # ── Camera (always on) ───────────────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="camera_node",
            name="camera_node",
            output="screen",
            parameters=[config],
        ),

        # ── Object detection (YOLO) ─────────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="detector_node",
            name="detector_node",
            output="screen",
            parameters=[config],
            condition=IfCondition(LaunchConfiguration("enable_detector")),
        ),

        # ── Monocular depth ─────────────────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="depth_node",
            name="depth_node",
            output="screen",
            parameters=[config],
            condition=IfCondition(LaunchConfiguration("enable_depth")),
        ),

        # ── Multi-object tracking ───────────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="tracker_node",
            name="tracker_node",
            output="screen",
            parameters=[config],
            condition=IfCondition(LaunchConfiguration("enable_tracker")),
        ),

        # ── 3D spatial fusion ───────────────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="spatial_node",
            name="spatial_node",
            output="screen",
            parameters=[config],
            condition=IfCondition(LaunchConfiguration("enable_spatial")),
        ),

        # ── Moondream VLM ───────────────────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="moondream_node",
            name="moondream_node",
            output="screen",
            parameters=[config],
            condition=IfCondition(LaunchConfiguration("enable_moondream")),
        ),

        # ── Periodic scene description ──────────────────────────────────
        Node(
            package="robot_vision_pkg",
            executable="scene_node",
            name="scene_node",
            output="screen",
            parameters=[config],
            condition=IfCondition(LaunchConfiguration("enable_scene")),
        ),
    ])
