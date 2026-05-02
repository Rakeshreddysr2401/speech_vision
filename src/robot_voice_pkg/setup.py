# from setuptools import setup, find_packages
# import os
# from glob import glob

# package_name = "robot_voice_pkg"

# setup(
#     name=package_name,
#     version="0.1.0",
#     packages=find_packages(exclude=["test"]),
#     data_files=[
#         ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
#         (f"share/{package_name}", ["package.xml"]),
#         (f"share/{package_name}/config", glob("config/*.yaml")),
#         (f"share/{package_name}/launch", glob("launch/*.py")),
#     ],
#     install_requires=["setuptools"],
#     zip_safe=True,
#     entry_points={
#         "console_scripts": [
#             f"stt_node = {package_name}.stt_node:main",
#             f"tts_node = {package_name}.tts_node:main",
#         ],
#     },
# )

from setuptools import setup, find_packages
from glob import glob

package_name = "robot_voice_pkg"

setup(
    name=package_name,
    version="0.1.0",

    packages=find_packages(exclude=["test"]),
    package_dir={"": "."},

    include_package_data=True,

    data_files=[
        ("share/ament_index/resource_index/packages",
            [f"resource/{package_name}"]),

        (f"share/{package_name}", ["package.xml"]),

        (f"share/{package_name}/config",
            glob("config/*.yaml")),

        (f"share/{package_name}/launch",
            glob("launch/*.py")),
    ],

    install_requires=["setuptools"],
    zip_safe=True,

    entry_points={
        "console_scripts": [
            "stt_node = robot_voice_pkg.stt_node:main",
            "tts_node = robot_voice_pkg.tts_node:main",
        ],
    },
)
