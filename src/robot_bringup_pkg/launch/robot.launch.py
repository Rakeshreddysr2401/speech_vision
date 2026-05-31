import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource


def _vision_launch(vision_launch_path, **overrides):
    return IncludeLaunchDescription(
        PythonLaunchDescriptionSource(vision_launch_path),
        launch_arguments=overrides.items(),
    )


def launch_setup(context, *args, **kwargs):
    mode = context.launch_configurations.get("mode", "visual_assistant")
    provider = context.launch_configurations.get("provider", "gemini")

    voice_launch = os.path.join(
        get_package_share_directory("robot_voice_pkg"), "launch", "voice.launch.py"
    )
    brain_launch = os.path.join(
        get_package_share_directory("robot_brain_pkg"), "launch", "brain.launch.py"
    )
    vision_launch = os.path.join(
        get_package_share_directory("robot_vision_pkg"), "launch", "vision.launch.py"
    )

    def voice():
        return IncludeLaunchDescription(PythonLaunchDescriptionSource(voice_launch))

    def brain(use_vision: str):
        return IncludeLaunchDescription(
            PythonLaunchDescriptionSource(brain_launch),
            launch_arguments={"provider": provider, "use_vision": use_vision}.items(),
        )

    def vision_minimal():
        # Camera + detector + depth + spatial + moondream — required for Pi5 integration.
        # depth_node provides /vision/depth for spatial_node to produce /vision/objects_3d.
        # moondream_node answers /vision/query → /vision/query_result for Pi5 VLM queries.
        return _vision_launch(
            vision_launch,
            enable_detector="true",
            enable_depth="false",
            enable_tracker="false",
            enable_spatial="false",
            enable_moondream="false",
            enable_scene="false",
        )

    def vision_full():
        return IncludeLaunchDescription(PythonLaunchDescriptionSource(vision_launch))

    if mode == "visual_assistant":
        # STT + TTS + camera + brain with VLM — your primary interactive mode
        return [voice(), brain("true"), vision_minimal()]

    elif mode == "voice_only":
        # STT + TTS + brain (text only, no camera)
        return [voice(), brain("false")]

    elif mode == "full":
        # Everything: voice + brain(VLM) + full vision pipeline
        return [voice(), brain("true"), vision_full()]

    elif mode == "perception_only":
        # Full vision pipeline, no voice or brain
        return [vision_full()]

    else:
        raise ValueError(
            f"Unknown mode: {mode!r}. "
            "Valid modes: visual_assistant | voice_only | full | perception_only"
        )


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument(
            "mode",
            default_value="visual_assistant",
            description=(
                "visual_assistant — STT + TTS + camera + brain(VLM)  [default]\n"
                "voice_only       — STT + TTS + brain (text only)\n"
                "full             — all nodes\n"
                "perception_only  — vision pipeline only"
            ),
        ),
        DeclareLaunchArgument(
            "provider",
            default_value="gemini",
            description="LLM backend: openai | llamacpp | gemini | ollama",
        ),
        OpaqueFunction(function=launch_setup),
    ])
