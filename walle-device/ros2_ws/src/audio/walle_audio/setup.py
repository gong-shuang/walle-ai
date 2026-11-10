import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'walle_audio'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*.*'))),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='gs',
    maintainer_email='gs@example.com',
    description='Voice module control node for Walle',
    license='Proprietary',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'speech = walle_audio.speech:main',
        ],
    },
)
