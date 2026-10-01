cmake_minimum_required(VERSION 3.16)
project(asio-proxy)

add_library(asio-proxy SHARED
    dllmain.cpp
    proxy-driver.cpp
    ../shared/direct-monitor.cpp
    ../shared/shared-memory.hpp
    ../shared/asio-types.hpp
)

target_include_directories(asio-proxy PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../shared
)

target_compile_features(asio-proxy PRIVATE cxx_std_17)
target_compile_definitions(asio-proxy PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN)

# ASIO runs on high-priority threads — make sure we use /O2 and no debug heap
target_compile_options(asio-proxy PRIVATE
    $<$<CONFIG:Release>:/O2>
    $<$<CONFIG:Debug>:/Od /Zi>
)

# Export DllGetClassObject / DllCanUnloadNow via .def file
set_target_properties(asio-proxy PROPERTIES
    WINDOWS_EXPORT_ALL_SYMBOLS OFF
    LINK_FLAGS "/DEF:\"${CMAKE_CURRENT_SOURCE_DIR}/asio-proxy.def\""
)

target_link_libraries(asio-proxy PRIVATE
    ole32 oleaut32 shlwapi uuid avrt
)
