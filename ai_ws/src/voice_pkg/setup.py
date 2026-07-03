from setuptools import find_packages, setup
import os
from glob import glob

package_name = 'voice_pkg'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'stt_node = voice_pkg.stt_node:main',
            'tts_node = voice_pkg.tts_node:main',
            'music_node = voice_pkg.music_node:main',
        ],
    },
)
