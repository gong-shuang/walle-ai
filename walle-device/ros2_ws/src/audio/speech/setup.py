#!/usr/bin/env python3
# encoding: utf-8
from setuptools import setup, find_packages

package_name = 'speech'

setup(
    name=package_name,
    version='1.0.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml', 'README.md']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='user',
    maintainer_email='user@example.com',
    description='Pure-Python drop-in replacement for Hiwonder speech.so',
    license='Proprietary',
    scripts=['scripts/speech_selfcheck.py'],
)
