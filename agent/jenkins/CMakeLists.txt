# agent/jenkins/ — Jenkins REST API client library

# Try find_package first, fall back to pkg-config
find_package(CURL QUIET)
if(NOT CURL_FOUND)
    find_package(PkgConfig QUIET)
    if(PkgConfig_FOUND)
        pkg_check_modules(CURL libcurl)
    endif()
endif()

# Manual fallback if both failed
if(NOT CURL_FOUND)
    set(CURL_INCLUDE_DIRS "/tmp/curl-dev/usr/include/x86_64-linux-gnu" CACHE PATH "libcurl include")
    set(CURL_LIBRARIES "/usr/lib/x86_64-linux-gnu/libcurl.so.4" CACHE FILEPATH "libcurl library")
endif()

add_library(jd_relay_jenkins STATIC
    src/jenkins_client.cpp
)

target_include_directories(jd_relay_jenkins PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CURL_INCLUDE_DIRS}
)

target_link_libraries(jd_relay_jenkins PUBLIC
    ${CURL_LIBRARIES}
)

# nlohmann_json
if(TARGET nlohmann_json::nlohmann_json)
    target_link_libraries(jd_relay_jenkins PUBLIC nlohmann_json::nlohmann_json)
else()
    target_include_directories(jd_relay_jenkins SYSTEM PUBLIC /usr/include)
endif()
