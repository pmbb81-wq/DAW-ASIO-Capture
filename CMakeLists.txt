cmake_minimum_required(VERSION 3.16)
project(obs-daw-capture-suite)

# Build both components together
add_subdirectory(asio-proxy)
add_subdirectory(obs-plugin)
