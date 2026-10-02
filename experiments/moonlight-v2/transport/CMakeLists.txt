cmake_minimum_required(VERSION 3.20)
project(huoguo_udp_transport_probe LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT MOONLIGHT_SOURCE_DIR)
  message(FATAL_ERROR "Pass the verified external Moonlight checkout through build.py")
endif()
# Compile the exact nanors sources used by the pinned Moonlight core, rather
# than a substitute XOR parity implementation or a copied vendored snapshot.
add_library(pinned_nanors STATIC
  "${MOONLIGHT_SOURCE_DIR}/nanors/rs.c"
  "${MOONLIGHT_SOURCE_DIR}/nanors/deps/obl/oblas_common.c"
  "${MOONLIGHT_SOURCE_DIR}/nanors/deps/obl/oblas_lite.c")
target_include_directories(pinned_nanors SYSTEM PUBLIC
  "${MOONLIGHT_SOURCE_DIR}/nanors"
  "${MOONLIGHT_SOURCE_DIR}/nanors/deps/obl")
add_executable(udp_media_probe udp_media_probe.cpp)
target_link_libraries(udp_media_probe PRIVATE pinned_nanors)
target_compile_options(udp_media_probe PRIVATE -Wall -Wextra -Werror)
find_package(Threads REQUIRED)
target_link_libraries(udp_media_probe PRIVATE Threads::Threads)
enable_testing()
add_test(NAME packet_loss_reorder_deadline COMMAND udp_media_probe --self-test)
add_executable(h264_udp_bridge h264_udp_bridge.cpp)
target_link_libraries(h264_udp_bridge PRIVATE pinned_nanors Threads::Threads)
target_compile_options(h264_udp_bridge PRIVATE -Wall -Wextra -Werror)
add_executable(h264_parser_probe h264_parser_probe.cpp)
target_link_libraries(h264_parser_probe PRIVATE pinned_nanors)
target_compile_options(h264_parser_probe PRIVATE -Wall -Wextra -Werror)
add_test(NAME framed_h264_parser COMMAND h264_parser_probe)
add_executable(recovery_feedback_probe recovery_feedback_probe.cpp)
target_compile_options(recovery_feedback_probe PRIVATE -Wall -Wextra -Werror)
add_test(NAME bounded_recovery_feedback COMMAND recovery_feedback_probe)
