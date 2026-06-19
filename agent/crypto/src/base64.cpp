#include "jd_relay/crypto/base64.h"
#include <openssl/evp.h>
#include <openssl/rand.h>
#include <openssl/bio.h>
#include <openssl/buffer.h>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <random>

namespace jd_relay::crypto {

// ── Base64 ────────────────────────────────────────────────────

std::string base64_encode(const uint8_t* data, size_t len) {
    BIO* b64  = BIO_new(BIO_f_base64());
    BIO* bmem = BIO_new(BIO_s_mem());
    b64 = BIO_push(b64, bmem);
    BIO_write(b64, data, static_cast<int>(len));
    BIO_flush(b64);

    BUF_MEM* bptr = nullptr;
    BIO_get_mem_ptr(b64, &bptr);

    std::string result(bptr->data, bptr->length);
    BIO_free_all(b64);
    return result;
}

std::string base64_encode(const std::vector<uint8_t>& data) {
    return base64_encode(data.data(), data.size());
}

std::vector<uint8_t> base64_decode(const std::string& encoded) {
    BIO* b64  = BIO_new(BIO_f_base64());
    BIO* bmem = BIO_new_mem_buf(encoded.data(), static_cast<int>(encoded.size()));
    b64 = BIO_push(b64, bmem);

    std::vector<uint8_t> result(encoded.size());
    int n = BIO_read(b64, result.data(), static_cast<int>(result.size()));
    if (n > 0) {
        result.resize(n);
    } else {
        result.clear();
    }
    BIO_free_all(b64);
    return result;
}

// ── Hex ───────────────────────────────────────────────────────

std::string hex_encode(const uint8_t* data, size_t len) {
    static const char hex[] = "0123456789abcdef";
    std::string result;
    result.reserve(len * 2);
    for (size_t i = 0; i < len; ++i) {
        result.push_back(hex[(data[i] >> 4) & 0xF]);
        result.push_back(hex[data[i] & 0xF]);
    }
    return result;
}

std::string hex_encode(const std::vector<uint8_t>& data) {
    return hex_encode(data.data(), data.size());
}

std::vector<uint8_t> hex_decode(const std::string& hex) {
    std::vector<uint8_t> result;
    result.reserve(hex.size() / 2);
    auto hex_val = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i + 1 < hex.size(); i += 2) {
        int hi = hex_val(hex[i]);
        int lo = hex_val(hex[i + 1]);
        if (hi < 0 || lo < 0) break;
        result.push_back(static_cast<uint8_t>((hi << 4) | lo));
    }
    return result;
}

// ── Random ────────────────────────────────────────────────────

std::vector<uint8_t> random_bytes(size_t count) {
    std::vector<uint8_t> buf(count);
    if (RAND_bytes(buf.data(), static_cast<int>(count)) != 1) {
        // Fallback to std::random_device (less ideal but better than nothing)
        std::random_device rd;
        for (size_t i = 0; i < count; ++i) {
            buf[i] = static_cast<uint8_t>(rd());
        }
    }
    return buf;
}

// ── UUID v4 ───────────────────────────────────────────────────

std::string generate_uuid() {
    auto rb = random_bytes(16);
    // Set version (4) and variant (RFC 4122)
    rb[6] = (rb[6] & 0x0F) | 0x40;
    rb[8] = (rb[8] & 0x3F) | 0x80;

    char buf[37];
    std::snprintf(buf, sizeof(buf),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        rb[0], rb[1], rb[2], rb[3],
        rb[4], rb[5], rb[6], rb[7],
        rb[8], rb[9], rb[10], rb[11],
        rb[12], rb[13], rb[14], rb[15]);
    return std::string(buf);
}

} // namespace jd_relay::crypto
