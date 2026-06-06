from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory
import os


def generate_launch_description():
    # Launch args — control which subsystems start
    # Usage examples:
    #   ros2 launch bringup_pkg robot.launch.py              (full stack)
    #   ros2 launch bringup_pkg robot.launch.py voice:=true vision:=false
    declare_voice = DeclareLaunchArgument('voice', default_value='true')
    declare_vision = DeclareLaunchArgument('vision', default_value='true')

    voice_launch = os.path.join(
        get_package_share_directory('voice_pkg'), 'launch', 'voice.launch.py'
    )
    vision_launch = os.path.join(
        get_package_share_directory('vision_pkg'), 'launch', 'vision.launch.py'
    )

    return LaunchDescription([
        declare_voice,
        declare_vision,

        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(voice_launch),
            condition=IfCondition(LaunchConfiguration('voice')),
        ),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(vision_launch),
            condition=IfCondition(LaunchConfiguration('vision')),
        ),
    ])
