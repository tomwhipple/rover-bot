# roverd: the rover's bus adapter. Needs robo-control (core/ and schemas/), zenoh-cpp with the
# zenoh-c backend, and protobuf.
cmake_minimum_required(VERSION 3.16)
project(roverd VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

get_filename_component(_rover_root "${CMAKE_CURRENT_SOURCE_DIR}/../.." ABSOLUTE)
set(ROBO_CONTROL_DIR "${_rover_root}/../robo-control" CACHE PATH "robo-control checkout")
option(ROBO_SANITIZE "Build with ASan and UBSan" OFF)
option(ROBO_WERROR "Treat warnings as errors" ON)

if(NOT EXISTS "${ROBO_CONTROL_DIR}/core/CMakeLists.txt")
  message(FATAL_ERROR "ROBO_CONTROL_DIR=${ROBO_CONTROL_DIR} has no core/CMakeLists.txt")
endif()

if(ROBO_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

add_subdirectory("${ROBO_CONTROL_DIR}/core" robo_core)
if(NOT TARGET robo_bus)
  message(FATAL_ERROR "zenoh-cpp with the zenoh-c backend is required (robo_bus not defined)")
endif()

# Protobuf: its CMake package where installed (recent releases), else CMake's FindProtobuf.
find_package(Protobuf CONFIG QUIET)
if(NOT Protobuf_FOUND)
  find_package(Protobuf REQUIRED)
endif()
if(TARGET protobuf::protoc)
  set(ROVERD_PROTOC $<TARGET_FILE:protobuf::protoc>)
else()
  set(ROVERD_PROTOC ${Protobuf_PROTOC_EXECUTABLE})
endif()

set(_proto "${ROBO_CONTROL_DIR}/schemas/robo_v1.proto")
set(_gen "${CMAKE_CURRENT_BINARY_DIR}/gen")
file(MAKE_DIRECTORY "${_gen}")
add_custom_command(
  OUTPUT "${_gen}/robo_v1.pb.cc" "${_gen}/robo_v1.pb.h"
  COMMAND ${ROVERD_PROTOC} -I "${ROBO_CONTROL_DIR}/schemas" --cpp_out "${_gen}" "${_proto}"
  DEPENDS "${_proto}"
  COMMENT "protoc robo_v1.proto"
  VERBATIM)

# Generated code is compiled without our warning flags.
add_library(robo_v1_proto STATIC "${_gen}/robo_v1.pb.cc")
target_include_directories(robo_v1_proto SYSTEM PUBLIC "${_gen}")
target_link_libraries(robo_v1_proto PUBLIC protobuf::libprotobuf)
if(ROBO_SANITIZE)
  # Under ASan, protobuf's generated code calls has-bit checks inside libprotobuf that crash
  # against a library not built with ASan. UBSan stays on.
  target_compile_options(robo_v1_proto PRIVATE -fno-sanitize=address)
endif()

set(_warn -Wall -Wextra -Wshadow -Wconversion -Wno-sign-conversion)
if(ROBO_WERROR)
  list(APPEND _warn -Werror)
endif()

add_library(roverd_lib STATIC src/protocol.cpp src/clock_map.cpp src/serial_port.cpp)
target_include_directories(roverd_lib PUBLIC src)
target_compile_options(roverd_lib PRIVATE ${_warn})

add_executable(roverd src/main.cpp src/roverd.cpp)
target_compile_definitions(roverd PRIVATE ROVERD_VERSION="${PROJECT_VERSION}")
target_compile_options(roverd PRIVATE ${_warn})
target_link_libraries(roverd PRIVATE roverd_lib robo_core robo_bus robo_v1_proto)

enable_testing()
add_executable(roverd_units tests/test_units.cpp)
target_compile_options(roverd_units PRIVATE ${_warn})
target_link_libraries(roverd_units PRIVATE roverd_lib)
add_test(NAME roverd_units COMMAND roverd_units)
