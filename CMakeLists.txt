cmake_minimum_required(VERSION 3.20)
project(dc)
set(CMAKE_CXX_STANDARD 17)
find_package(CURL REQUIRED)
include_directories(src/include)
build_loadable_extension(dc "" src/dc_extension.cpp)
target_link_libraries(dc_loadable_extension CURL::libcurl)
if(DC_DUCKDB_1_4)
  target_compile_definitions(dc_loadable_extension
                             PRIVATE DC_DUCKDB_1_4=1)
endif()
if(DC_PREBUILT_DUCKDB_STATIC_DIR)
  target_link_libraries(dc_loadable_extension
                        "${DC_PREBUILT_DUCKDB_STATIC_DIR}/libduckdb_static.a")
  if(DC_DUCKDB_1_4)
    file(GLOB DC_DUCKDB_DEPENDENCY_LIBS
         "${DC_PREBUILT_DUCKDB_STATIC_DIR}/*.a")
    list(REMOVE_ITEM DC_DUCKDB_DEPENDENCY_LIBS
         "${DC_PREBUILT_DUCKDB_STATIC_DIR}/libduckdb_static.a")
    target_link_libraries(dc_loadable_extension
                          ${DC_DUCKDB_DEPENDENCY_LIBS})
  else()
    target_link_libraries(dc_loadable_extension
                          dummy_static_extension_loader)
  endif()
  if(UNIX AND NOT APPLE)
    set_target_properties(dc_loadable_extension
                          PROPERTIES CXX_VISIBILITY_PRESET hidden)
    target_link_options(dc_loadable_extension PRIVATE -Wl,--gc-sections
                        -Wl,--exclude-libs,ALL)
  endif()
endif()
