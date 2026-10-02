#pragma once
#include <chrono>
#include <cstdint>
#include <stdexcept>
#if defined(__APPLE__)
#include <time.h>
#endif

namespace huoguo::android_udp {
// HGUD's host capture/deadline clock must match the Python sender, whose
// time.monotonic_ns() is mach_absolute_time on Darwin. libc++ steady_clock
// instead uses CLOCK_MONOTONIC_RAW (continuous across host sleep). These
// clocks may coincide before the first sleep, but are not interchangeable.
#if defined(__APPLE__)
inline constexpr const char* HostClockDomain = "host_clock_gettime_CLOCK_UPTIME_RAW_us";
inline uint64_t hostMonotonicUs() {
    timespec value{};
    if (clock_gettime(CLOCK_UPTIME_RAW, &value) != 0) {
        throw std::runtime_error("host uptime clock unavailable");
    }
    return uint64_t(value.tv_sec) * 1000000ULL + uint64_t(value.tv_nsec) / 1000ULL;
}
#else
inline constexpr const char* HostClockDomain = "host_std_chrono_steady_clock_us";
inline uint64_t hostMonotonicUs() {
    return uint64_t(std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count());
}
#endif
} // namespace huoguo::android_udp
