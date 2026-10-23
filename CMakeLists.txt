cmake_minimum_required(VERSION 3.20)
project(dc)
set(CMAKE_CXX_STANDARD 17)
find_package(CURL REQUIRED)
include_directories(src/include)
set(DC_EXTENSION_SOURCES src/dc_extension.cpp)
build_static_extension(dc ${DC_EXTENSION_SOURCES})
build_loadable_extension(dc "" ${DC_EXTENSION_SOURCES})
target_link_libraries(dc_extension CURL::libcurl)
target_link_libraries(dc_loadable_extension CURL::libcurl)
if(DUCKDB_VERSION_NUMBER VERSION_GREATER_EQUAL "2.0.0")
  target_compile_definitions(dc_loadable_extension PRIVATE DC_DUCKDB_2=1)
  target_compile_definitions(dc_extension PRIVATE DC_DUCKDB_2=1)
endif()
if(DUCKDB_VERSION_NUMBER VERSION_LESS "1.5.0")
  set(DC_DUCKDB_1_4 ON)
  target_compile_definitions(dc_extension PRIVATE DC_DUCKDB_1_4=1)
  target_compile_definitions(dc_loadable_extension
                             PRIVATE DC_DUCKDB_1_4=1)
else()
  set(DC_DUCKDB_1_4 OFF)
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
  elseif(DUCKDB_VERSION_NUMBER VERSION_LESS "2.0.0")
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

install(
  TARGETS dc_extension
  EXPORT "${DUCKDB_EXPORT_SET}"
  LIBRARY DESTINATION "${INSTALL_LIB_DIR}"
  ARCHIVE DESTINATION "${INSTALL_LIB_DIR}")
