cmake_minimum_required(VERSION 3.16)
project(obs-daw-capture)

# ---- OBS dependencies (pre-fetched into deps/) ------------------------------
# Headers: deps/obs-studio/libobs/   (sparse-cloned from obsproject/obs-studio)
# Lib:     deps/obs.lib              (import lib generated from installed obs.dll)

set(DEPS_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../deps")

set(OBS_INCLUDE_DIR "${DEPS_DIR}/obs-studio/libobs")
set(OBS_LIB         "${DEPS_DIR}/obs.lib")

if(NOT EXISTS "${OBS_INCLUDE_DIR}/obs-module.h")
    message(FATAL_ERROR "OBS headers not found at ${OBS_INCLUDE_DIR}. Run: cd deps/obs-studio && git sparse-checkout set libobs")
endif()
if(NOT EXISTS "${OBS_LIB}")
    message(FATAL_ERROR "obs.lib not found at ${OBS_LIB}. Re-run the setup script.")
endif()

message(STATUS "OBS headers : ${OBS_INCLUDE_DIR}")
message(STATUS "OBS library : ${OBS_LIB}")

# ---- Plugin target ----------------------------------------------------------
add_library(obs-daw-capture SHARED
    plugin-main.cpp
    daw-source.cpp
    asio-capture.cpp
    asio-registry.cpp
    daw-source.hpp
    asio-capture.hpp
    asio-registry.hpp
    ../shared/shared-memory.hpp
    ../shared/asio-types.hpp
)

target_include_directories(obs-daw-capture PRIVATE
    ${OBS_INCLUDE_DIR}
    ${DEPS_DIR}              # obsconfig.h lives here
    ${CMAKE_CURRENT_SOURCE_DIR}/../shared
)

target_compile_features(obs-daw-capture PRIVATE cxx_std_20)
target_compile_definitions(obs-daw-capture PRIVATE UNICODE _UNICODE WIN32_LEAN_AND_MEAN)

target_link_libraries(obs-daw-capture PRIVATE
    ${OBS_LIB}
    ole32 oleaut32 shlwapi uuid
)

# ---- Install ----------------------------------------------------------------
# Copy both DLLs to the OBS plugin directory
if(DEFINED OBS_PLUGIN_DIR)
    install(TARGETS obs-daw-capture RUNTIME DESTINATION "${OBS_PLUGIN_DIR}/64bit")
    install(FILES
        $<TARGET_FILE_DIR:obs-daw-capture>/../asio-proxy/asio-proxy.dll
        DESTINATION "${OBS_PLUGIN_DIR}/64bit"
    )
endif()
