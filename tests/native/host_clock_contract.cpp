// Pure clock-source contract; no device, socket, sleeping or real deadline.
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <time.h>

// Inject a simulated Darwin clock with 10 seconds of uptime and 100 seconds
// of continuous time. Selecting RAW rather than UPTIME_RAW must fail even
// though the real machine might not yet have accumulated any host sleep.
#ifndef CLOCK_UPTIME_RAW
#define CLOCK_UPTIME_RAW 8
#endif
#ifndef CLOCK_MONOTONIC_RAW
#define CLOCK_MONOTONIC_RAW 4
#endif
static int requestedClock = -1;
static bool rejectClock = false;
static int contract_clock_gettime(int clock, timespec* value) {
    requestedClock = clock;
    if (rejectClock) return -1;
    value->tv_sec = clock == CLOCK_UPTIME_RAW ? 10 : 100;
    value->tv_nsec = 987654321;
    return 0;
}
#ifndef __APPLE__
#define __APPLE__ 1
#endif
#define clock_gettime contract_clock_gettime
#include "host_clock.hpp"
#undef clock_gettime

int main() {
    using namespace huoguo::android_udp;
    if (hostMonotonicUs() != 10987654 || requestedClock != CLOCK_UPTIME_RAW) return 1;
    rejectClock = true;
    try { (void)hostMonotonicUs(); } catch (const std::runtime_error&) { return 0; }
    return 2; // A failed read cannot silently return zero or change epoch.
}
