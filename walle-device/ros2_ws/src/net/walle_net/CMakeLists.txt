cmake_minimum_required(VERSION 3.8)
project(walle_net)

find_package(ament_cmake REQUIRED)

install(DIRECTORY launch
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
