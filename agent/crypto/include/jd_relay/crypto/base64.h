#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace jd_relay::crypto {

/// Base64 encode/decode utilities.
/// Uses URL-safe alphabet without padding for nonce/iv fields,
/// standard alphabet with padding for ciphertext/tag/signature.

/// Standard base64 encode (with padding).
std::string base64_encode(const uint8_t* data, size_t len);
std::string base64_encode(const std::vector<uint8_t>& data);

/// Standard base64 decode.
std::vector<uint8_t> base64_decode(const std::string& encoded);

/// Hex encode/decode.
std::string hex_encode(const uint8_t* data, size_t len);
std::string hex_encode(const std::vector<uint8_t>& data);
std::vector<uint8_t> hex_decode(const std::string& hex);

/// Generate a random byte sequence using OpenSSL CSPRNG.
std::vector<uint8_t> random_bytes(size_t count);

/// Generate a UUID v4 string.
std::string generate_uuid();

} // namespace jd_relay::crypto
