import yaml
import os
from ament_index_python.packages import get_package_share_directory

lab_file_path = os.path.join( get_package_share_directory('sdk'),
        'config', 'lab_config.yaml')
servo_file_path = os.path.join( get_package_share_directory('sdk'),
        'config', 'servo_config.yaml')


def get_yaml_data(yaml_file):
    file = open(yaml_file, 'r', encoding='utf-8')
    file_data = file.read()
    file.close()
    
    data = yaml.load(file_data, Loader=yaml.FullLoader)
    
    return data

def save_yaml_data(data, yaml_file):
    file = open(yaml_file, 'w', encoding='utf-8')
    yaml.dump(data, file)
    file.close()
