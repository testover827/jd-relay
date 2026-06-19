#pragma once

#include <string>
#include <fstream>
#include <sstream>
#include <stdexcept>

namespace jd_relay::protocol {

/// Read entire file contents as a string.
inline std::string read_file(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) throw std::runtime_error("Cannot open file: " + path);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

} // namespace jd_relay::protocol
