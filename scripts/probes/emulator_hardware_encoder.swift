// Complete RGBA frames from stdin -> required Apple H.264 hardware encoder.
// No ADB, gRPC, screen capture, network access, or service changes are performed here.
// Build: xcrun swiftc -O -module-cache-path /private/tmp/huoguo-swift-cache \
//   scripts/probes/emulator_hardware_encoder.swift -o /private/tmp/huoguo-emulator-hw-encoder
// Input: repeated BE [width:u32][height:u32][ptsUs:u64][length:u32][RGBA bytes].
// Output: "h264", BE [0x80000000:u32][width:u32][height:u32], then repeated
// BE [ptsAndFlags:u64][length:u32][Annex B]. config=bit62, keyframe=bit61.
// Metrics are JSON on stderr. stdout is exclusively binary client-compatible video.
// complete_rgba_to_callback_ms starts only after the full frame has been read
// from stdin; it excludes gRPC capture delivery, the RGBA pipe, and networking.
// hardware_stream.py provides the bounded latest-frame queue in service mode.
// Default probe mode is bounded by frames/time. --service removes those lifetime
// limits; both modes retain 3 in-flight frames and a 2-second write timeout.
// Resolution changes rebuild the encoder without repeating the codec header.
// --low-latency-mode true selects VT's dedicated H264 low-latency mode.
// The default false preserves the original encoder selection/profile. This is
// an isolated experiment flag; it does not change any deployed native binary.
// Optional paired --burst-bytes / --burst-seconds adds a second DataRateLimits
// window in VBR mode. The default retains only the original one-second window.
// Limits apply to compressed payload in decode time, not FEC/encrypted wire
// bytes or a guaranteed maximum size for an individual IDR.
// --enable-ltr true is an isolated macOS12+ experiment. Default false never
// sets EnableLTR. With service+LTR, a 0x0 frame header of length12 carries
// BE [type:u32][token:u64]: 3 ACK an emitted token, 4 request LTR refresh (0).
// ACK is supplied by the caller after receiver acceptance, never auto-generated.
// --ltr-allow-hardware-wrapper true additionally permits the exact RTVC wrapper
// only with explicit LTR+lowLatency+RequireHardware creation. Apple WWDC21 says
// dedicated low-latency encoding is hardware-only. Unsupported hardware-property
// readback/absent registry marker remain reported, never converted to readback=true.
// Optional --prioritize-speed true|false selects Apple's macOS11+ encoding
// speed/quality hint. Omission leaves the property unset (Apple's NULL default),
// which is distinct from explicitly selecting false. Support, setter and typed
// readback evidence are reported; acceptance does not prove faster encoding.
// Optional --pixel-pool manual|session changes only the source pool origin.
// Default manual retains the separately created BGRA IOSurface pool. Session
// uses VTCompressionSessionGetPixelBufferPool and fails on absent or mismatched
// BGRA/size attributes or buffers, without converting to another input format.

import Foundation
import CoreVideo
import CoreMedia
import VideoToolbox
import Accelerate
import Darwin

struct EncoderFailure: Error, CustomStringConvertible { let description: String }
enum PixelPoolMode: String { case manual, session }

func verifiedBGRABuffer(_ pixel: CVPixelBuffer, width: Int, height: Int) throws {
    guard CVPixelBufferGetPixelFormatType(pixel) == kCVPixelFormatType_32BGRA,
          CVPixelBufferGetWidth(pixel) == width, CVPixelBufferGetHeight(pixel) == height,
          !CVPixelBufferIsPlanar(pixel), CVPixelBufferGetBytesPerRow(pixel) >= width * 4 else {
        throw EncoderFailure(description: "pixel pool buffer does not match requested packed BGRA dimensions/stride")
    }
}

func verifiedPixelPool(_ pool: CVPixelBufferPool, mode: PixelPoolMode,
                       width: Int, height: Int) throws -> [String: Any] {
    guard let attributes = CVPixelBufferPoolGetPixelBufferAttributes(pool) as? [String: Any] else {
        throw EncoderFailure(description: "pixel pool attributes unavailable for \(mode.rawValue) pool")
    }
    func matches(_ key: CFString, _ expected: Int) -> Bool {
        guard let value = attributes[key as String] as? NSNumber,
              CFGetTypeID(value) != CFBooleanGetTypeID() else { return false }
        return value.doubleValue == Double(expected) && value.int64Value == Int64(expected)
    }
    let format = Int(kCVPixelFormatType_32BGRA)
    guard matches(kCVPixelBufferPixelFormatTypeKey, format),
          matches(kCVPixelBufferWidthKey, width), matches(kCVPixelBufferHeightKey, height) else {
        throw EncoderFailure(description: "pixel pool attributes do not match requested BGRA dimensions for \(mode.rawValue) pool")
    }
    // Read a real buffer as well as the pool dictionary before declaring ready.
    // Both modes use the same startup check; no image is submitted or converted.
    var buffer: CVPixelBuffer?
    try check(CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &buffer), "verify \(mode.rawValue) pixel pool")
    guard let pixel = buffer else { throw EncoderFailure(description: "pixel pool verification returned no buffer") }
    try verifiedBGRABuffer(pixel, width: width, height: height)
    return ["pixel_pool_mode": mode.rawValue, "pixel_pool_attributes_verified": true,
            "pixel_pool_pixel_format_readback": attributes[kCVPixelBufferPixelFormatTypeKey as String]!,
            "pixel_pool_width_readback": attributes[kCVPixelBufferWidthKey as String]!,
            "pixel_pool_height_readback": attributes[kCVPixelBufferHeightKey as String]!,
            "pixel_pool_buffer_verified": true,
            "pixel_pool_probe_pixel_format": Int(CVPixelBufferGetPixelFormatType(pixel)),
            "pixel_pool_probe_width": CVPixelBufferGetWidth(pixel),
            "pixel_pool_probe_height": CVPixelBufferGetHeight(pixel),
            "pixel_pool_probe_bytes_per_row": CVPixelBufferGetBytesPerRow(pixel)]
}

func validateBurstLimit(bytes: Int?, seconds: Double?, mode: String) throws {
    guard (bytes == nil) == (seconds == nil) else {
        throw EncoderFailure(description: "burst-bytes and burst-seconds must be supplied together")
    }
    guard let bytes = bytes, let seconds = seconds else { return }
    guard mode == "VBR" else { throw EncoderFailure(description: "burst window requires VBR; DataRateLimits is incompatible with CBR") }
    guard (32768...2_000_000).contains(bytes), seconds.isFinite, (0.02...1.0).contains(seconds) else {
        throw EncoderFailure(description: "invalid burst window: bytes 32768...2000000; seconds finite 0.02...1.0")
    }
}
func check(_ status: OSStatus, _ action: String) throws {
    if status != noErr { throw EncoderFailure(description: "\(action): OSStatus \(status)") }
}
func nowNS() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
// Trace uses the same explicitly selected host clock as Python's
// time.clock_gettime_ns(time.CLOCK_MONOTONIC), not an assumed DispatchTime epoch.
func traceClockNS() -> UInt64 {
    var value = timespec()
    precondition(clock_gettime(CLOCK_MONOTONIC, &value) == 0)
    return UInt64(value.tv_sec) * 1_000_000_000 + UInt64(value.tv_nsec)
}
final class BoundedCaptureTrace {
    let fd: Int32
    let lock = NSLock(), writer = DispatchQueue(label: "huoguo.capture.trace", qos: .utility)
    let maxRecords = 24000, maxBytes = 16 * 1024 * 1024, maxPending = 256
    var accepted = 0, written = 0, pending = 0, dropped = 0, bytes = 0
    var closed = false, failed = false, byteCapped = false
    init(path: String) throws {
        fd = Darwin.open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, S_IRUSR | S_IWUSR)
        guard fd >= 0 else { throw EncoderFailure(description: "private trace file creation failed: errno \(errno)") }
        _ = fchmod(fd, S_IRUSR | S_IWUSR)
        record(clockSample(phase: "start"))
    }
    func clockSample(phase: String) -> [String: Any] {
        let before = traceClockNS(), wall = UInt64(Date().timeIntervalSince1970 * 1e9), after = traceClockNS()
        return ["event": "trace_clock", "phase": phase,
            "clock_domain": "host_clock_gettime_CLOCK_MONOTONIC_ns",
            "clock_before_ns": before, "clock_after_ns": after, "unix_ns": wall,
            "wall_clock_precision_ns": 1024,
            "source_pts_scope": "emulator_estimated_screenshot_generation_unix_us_not_guest_media_pts"]
    }
    func record(_ fields: [String: Any]) {
        lock.lock()
        guard !closed else { lock.unlock(); return }
        guard !failed, accepted < maxRecords, pending < maxPending else {
            dropped += 1; lock.unlock(); return
        }
        accepted += 1; pending += 1
        var entry = fields
        entry["schema"] = "capture-vt-trace-v1"; entry["process"] = "swift"
        let immutableEntry = entry
        writer.async { [self] in
            defer { lock.lock(); pending -= 1; lock.unlock() }
            guard var encoded = try? JSONSerialization.data(withJSONObject: immutableEntry, options: [.sortedKeys]) else {
                lock.lock(); failed = true; lock.unlock(); return
            }
            encoded.append(10)
            guard bytes + encoded.count <= maxBytes - 2048 else { byteCapped = true; return }
            if write(encoded) { bytes += encoded.count; written += 1 }
        }
        lock.unlock()
    }
    private func write(_ data: Data) -> Bool {
        var offset = 0
        while offset < data.count {
            let n = data.withUnsafeBytes { Darwin.write(fd, $0.baseAddress!.advanced(by: offset), data.count - offset) }
            if n < 0 && errno == EINTR { continue }
            if n <= 0 { lock.lock(); failed = true; lock.unlock(); return false }
            offset += n
        }
        return true
    }
    func finish() {
        record(clockSample(phase: "end"))
        lock.lock(); if closed { lock.unlock(); return }; closed = true; lock.unlock()
        writer.sync {
            lock.lock()
            let summary: [String: Any] = ["schema": "capture-vt-trace-v1", "process": "swift", "event": "trace_summary",
                "accepted_records": accepted, "written_records": written, "dropped_records": dropped,
                "byte_capped": byteCapped, "record_limit": maxRecords, "byte_limit": maxBytes,
                "failed": failed, "clean_close": true]
            lock.unlock()
            if var data = try? JSONSerialization.data(withJSONObject: summary, options: [.sortedKeys]) {
                data.append(10); _ = write(data)
            }
            _ = Darwin.close(fd)
        }
    }
}
func ms(_ start: UInt64, _ end: UInt64) -> Double { Double(end >= start ? end - start : 0) / 1e6 }
func cpuSeconds() -> Double {
    var r = rusage(); if getrusage(RUSAGE_SELF, &r) != 0 { return 0 }
    return Double(r.ru_utime.tv_sec + r.ru_stime.tv_sec) + Double(r.ru_utime.tv_usec + r.ru_stime.tv_usec) / 1e6
}
func distribution(_ values: [Double]) -> [String: Any] {
    let s = values.sorted(); if s.isEmpty { return ["count": 0] }
    return ["count": s.count, "p50": s[min(s.count - 1, Int(ceil(Double(s.count) * 0.5)) - 1)],
            "p95": s[min(s.count - 1, Int(ceil(Double(s.count) * 0.95)) - 1)], "max": s.last!]
}
func logJSON(_ values: [String: Any]) {
    if let bytes = try? JSONSerialization.data(withJSONObject: values, options: [.sortedKeys]) {
        FileHandle.standardError.write(bytes); FileHandle.standardError.write(Data([10]))
    }
}
func appendBE<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    var be = value.bigEndian; withUnsafeBytes(of: &be) { data.append(contentsOf: $0) }
}
func unsignedBE(_ data: Data, _ start: Int, _ length: Int) -> UInt64 {
    var value: UInt64 = 0
    for offset in start..<(start + length) { value = (value << 8) | UInt64(data[offset]) }
    return value
}
func waitFD(_ fd: Int32, _ events: Int16, _ timeoutMS: Int32) throws {
    var p = pollfd(fd: fd, events: events, revents: 0)
    while true {
        let result = poll(&p, 1, timeoutMS)
        if result < 0 && errno == EINTR { continue }
        if result < 0 { throw EncoderFailure(description: "poll fd \(fd): errno \(errno)") }
        if result == 0 { throw EncoderFailure(description: "fd \(fd) timeout after \(timeoutMS) ms") }
        if p.revents & Int16(POLLERR | POLLNVAL) != 0 { throw EncoderFailure(description: "fd \(fd) poll error") }
        return
    }
}
func readExact(_ count: Int, idleMS: Int32, deadlineNS: UInt64, cleanEOF: Bool = false) throws -> Data? {
    var data = Data(count: count), offset = 0
    while offset < count {
        let current = nowNS()
        if current >= deadlineNS { throw EncoderFailure(description: "input exceeded prototype total time limit") }
        let remainingMS = Int32(max(1, min(UInt64(idleMS), (deadlineNS - current) / 1_000_000)))
        try waitFD(STDIN_FILENO, Int16(POLLIN), remainingMS)
        let readCount = data.withUnsafeMutableBytes { bytes in
            Darwin.read(STDIN_FILENO, bytes.baseAddress!.advanced(by: offset), count - offset)
        }
        if readCount < 0 && (errno == EINTR || errno == EAGAIN) { continue }
        if readCount < 0 { throw EncoderFailure(description: "stdin read: errno \(errno)") }
        if readCount == 0 {
            if cleanEOF && offset == 0 { return nil }
            throw EncoderFailure(description: "truncated input frame")
        }
        offset += readCount
    }
    return data
}
func writeAll(_ data: Data) throws {
    var offset = 0
    let deadline = nowNS() + 2_000_000_000
    while offset < data.count {
        let current = nowNS()
        if current >= deadline { throw EncoderFailure(description: "stdout write exceeded 2 seconds") }
        let timeout = Int32(max(1, min(2000, (deadline - current) / 1_000_000)))
        try waitFD(STDOUT_FILENO, Int16(POLLOUT), timeout)
        let n = data.withUnsafeBytes { bytes in
            Darwin.write(STDOUT_FILENO, bytes.baseAddress!.advanced(by: offset), data.count - offset)
        }
        if n < 0 && (errno == EINTR || errno == EAGAIN) { continue }
        if n <= 0 { throw EncoderFailure(description: "stdout write: errno \(errno)") }
        offset += n
    }
}
func sessionProperty(_ session: VTCompressionSession, _ key: CFString) throws -> Any? {
    var value: Unmanaged<CFTypeRef>?
    try check(VTSessionCopyProperty(session, key: key, allocator: kCFAllocatorDefault, valueOut: &value), "read \(key)")
    return value?.takeRetainedValue()
}

struct PrioritizeSpeedEvidence {
    let requested: Bool?
    var apiAvailable = false
    var supportedPropertiesStatus: Int?
    var supported: Bool?
    var setStatus: Int?
    var readStatus: Int?
    var readback: Bool?

    mutating func configure(_ session: VTCompressionSession) {
        guard #available(macOS 11.0, *) else { return }
        apiAvailable = true
        let key: CFString = kVTCompressionPropertyKey_PrioritizeEncodingSpeedOverQuality
        var properties: CFDictionary?
        let status = VTSessionCopySupportedPropertyDictionary(session, supportedPropertyDictionaryOut: &properties)
        supportedPropertiesStatus = Int(status)
        if status == noErr, let properties = properties {
            supported = CFDictionaryContainsKey(properties,
                UnsafeRawPointer(Unmanaged.passUnretained(key).toOpaque()))
        }
        // Never set NULL or false for the omitted CLI option. Only a declared
        // supported property receives an explicit true/false request.
        if supported == true, let requested = requested {
            setStatus = Int(VTSessionSetProperty(session, key: key,
                value: requested ? kCFBooleanTrue : kCFBooleanFalse))
        }
    }

    mutating func readBack(_ session: VTCompressionSession) {
        readback = nil
        guard #available(macOS 11.0, *) else { return }
        var reference: Unmanaged<CFTypeRef>?
        let status = VTSessionCopyProperty(session,
            key: kVTCompressionPropertyKey_PrioritizeEncodingSpeedOverQuality,
            allocator: kCFAllocatorDefault, valueOut: &reference)
        readStatus = Int(status)
        let value = reference?.takeRetainedValue()
        // An arbitrary NSNumber is not evidence of Apple's CFBoolean property.
        // The documented NULL default also remains null, never assumed false.
        if status == noErr, let value = value,
           CFGetTypeID(value) == CFBooleanGetTypeID() {
            readback = CFEqual(value, kCFBooleanTrue)
        }
    }

    var status: String {
        guard let requested = requested else { return "not_requested" }
        guard apiAvailable else { return "api_unavailable" }
        guard supportedPropertiesStatus == Int(noErr), let supported = supported else {
            return "supported_properties_query_failed"
        }
        guard supported else { return "unsupported" }
        if setStatus == Int(kVTPropertyNotSupportedErr) { return "unsupported" }
        guard setStatus == Int(noErr) else { return "set_failed" }
        if readStatus == Int(kVTPropertyNotSupportedErr) { return "unsupported" }
        guard readStatus == Int(noErr) else { return "read_failed" }
        guard let readback = readback else { return "readback_not_boolean" }
        return readback == requested ? "confirmed" : "readback_mismatch"
    }

    var metadata: [String: Any] {
        return [
            "prioritize_speed_requested": requested as Any? ?? NSNull(),
            "prioritize_speed_supported_properties_status": supportedPropertiesStatus as Any? ?? NSNull(),
            "prioritize_speed_supported": supported as Any? ?? NSNull(),
            "prioritize_speed_set_status": setStatus as Any? ?? NSNull(),
            "prioritize_speed_read_status": readStatus as Any? ?? NSNull(),
            "prioritize_speed_readback": readback as Any? ?? NSNull(),
            "prioritize_speed_status": status
        ]
    }
}

final class FrameContext {
    let pts: UInt64, rawReadyNS: UInt64, submitNS: UInt64, pixel: CVPixelBuffer
    let traceFields: [String: Any]?
    init(_ pts: UInt64, _ rawReadyNS: UInt64, _ submitNS: UInt64, _ pixel: CVPixelBuffer, traceFields: [String: Any]? = nil) {
        self.pts = pts; self.rawReadyNS = rawReadyNS; self.submitNS = submitNS; self.pixel = pixel
        self.traceFields = traceFields
    }
}
let outputCallback: VTCompressionOutputCallback = { context, source, status, flags, sample in
    guard let context = context, let source = source else { return }
    let encoder = Unmanaged<HardwareEncoder>.fromOpaque(context).takeUnretainedValue()
    let ticket = Unmanaged<FrameContext>.fromOpaque(source).takeRetainedValue()
    encoder.deliver(ticket, status, flags, sample)
}

final class HardwareEncoder {
    let width: Int, height: Int, fps: Int, bitrate: Int
    let bitrateMode: String
    let lowLatencyMode: Bool, profileLevel: CFString
    let burstBytes: Int?, burstSeconds: Double?
    let ltrEnabled: Bool
    let ltrAllowHardwareWrapper: Bool
    let pixelPoolMode: PixelPoolMode
    let captureTrace: BoundedCaptureTrace?
    let startedNS = nowNS(), cpuStart = cpuSeconds()
    let lock = NSLock(), outputLock = NSLock(), slots = DispatchSemaphore(value: 3)
    var session: VTCompressionSession?, pool: CVPixelBufferPool?
    var submitted = 0, encoded = 0, dropped = 0, keyframes = 0, bytesOutput = 0, pending = 0, pendingPeak = 0
    var conversionMS: [Double] = [], encodeMS: [Double] = [], fullMS: [Double] = [], writeMS: [Double] = []
    var captureAgeMS: [Double] = [], outputTimesNS: [UInt64] = []
    var errorMessage: String?, previousConfig = Data(), previousPTS: UInt64?
    var frameDelaySetStatus: [String: Int] = [:]
    var rateLimitStatus: Int?
    var bitrateSetStatus: Int?, currentBitrate: Int
    var prioritizeSpeedEvidence = PrioritizeSpeedEvidence(requested: nil)
    var pixelPoolEvidence: [String: Any] = [:]
    var forceKeyframe = false
    var forceLTRRefresh = false
    var acknowledgedLTRTokens: [NSNumber] = []
    var emittedLTRTokens: Set<Int64> = []

    init(width: Int, height: Int, fps: Int, bitrate: Int, bitrateMode: String = "VBR", emitCodec: Bool = true,
         lowLatencyMode: Bool = false, burstBytes: Int? = nil, burstSeconds: Double? = nil,
         enableLTR: Bool = false, ltrAllowHardwareWrapper: Bool = false, captureTrace: BoundedCaptureTrace? = nil,
         prioritizeSpeed: Bool? = nil, pixelPoolMode: PixelPoolMode = .manual) throws {
        self.width = width; self.height = height; self.fps = fps; self.bitrate = bitrate
        self.bitrateMode = bitrateMode; self.lowLatencyMode = lowLatencyMode
        self.burstBytes = burstBytes; self.burstSeconds = burstSeconds; self.currentBitrate = bitrate
        self.ltrEnabled = enableLTR
        self.ltrAllowHardwareWrapper = ltrAllowHardwareWrapper
        self.pixelPoolMode = pixelPoolMode
        self.captureTrace = captureTrace
        self.prioritizeSpeedEvidence = PrioritizeSpeedEvidence(requested: prioritizeSpeed)
        guard !ltrAllowHardwareWrapper || enableLTR && lowLatencyMode else {
            throw EncoderFailure(description: "LTR hardware wrapper experiment requires enable-ltr and low-latency-mode true")
        }
        try validateBurstLimit(bytes: burstBytes, seconds: burstSeconds, mode: bitrateMode)
        if lowLatencyMode {
            // The current SDK specifies High profiles for this dedicated mode.
            // Fail on older systems rather than silently reverting to Baseline
            // or dropping the requested low-latency specification.
            guard #available(macOS 12.0, *) else {
                throw EncoderFailure(description: "low-latency mode with ConstrainedHigh requires macOS 12 or later")
            }
            profileLevel = kVTProfileLevel_H264_ConstrainedHigh_AutoLevel
        } else {
            profileLevel = kVTProfileLevel_H264_Baseline_AutoLevel
        }
        let pixelAttributes: [CFString: Any] = [
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey: width, kCVPixelBufferHeightKey: height,
            kCVPixelBufferIOSurfacePropertiesKey: [:]
        ]
        var specification: [CFString: Any] = [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: kCFBooleanTrue!]
        if lowLatencyMode {
            if #available(macOS 11.3, *) {
                specification[kVTVideoEncoderSpecification_EnableLowLatencyRateControl] = kCFBooleanTrue!
            } else {
                throw EncoderFailure(description: "dedicated VT low-latency encoder specification is unavailable")
            }
        }
        try check(VTCompressionSessionCreate(allocator: kCFAllocatorDefault, width: Int32(width), height: Int32(height),
            codecType: kCMVideoCodecType_H264, encoderSpecification: specification as CFDictionary,
            imageBufferAttributes: pixelAttributes as CFDictionary, compressedDataAllocator: nil,
            outputCallback: outputCallback, refcon: Unmanaged.passUnretained(self).toOpaque(),
            compressionSessionOut: &session), "create required hardware H264 (low-latency-mode=\(lowLatencyMode))")
        guard let session = session else { throw EncoderFailure(description: "missing VT session") }
        let properties: [(CFString, CFTypeRef)] = [
            (kVTCompressionPropertyKey_RealTime, kCFBooleanTrue),
            (kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse),
            (kVTCompressionPropertyKey_ProfileLevel, profileLevel),
            (kVTCompressionPropertyKey_ExpectedFrameRate, NSNumber(value: fps)),
            (kVTCompressionPropertyKey_AverageBitRate, NSNumber(value: bitrate)),
            (kVTCompressionPropertyKey_MaxKeyFrameInterval, NSNumber(value: fps * 2)),
            (kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, NSNumber(value: 2.0)),
            (kVTCompressionPropertyKey_ColorPrimaries, kCVImageBufferColorPrimaries_ITU_R_709_2),
            (kVTCompressionPropertyKey_TransferFunction, kCVImageBufferTransferFunction_ITU_R_709_2),
            (kVTCompressionPropertyKey_YCbCrMatrix, kCVImageBufferYCbCrMatrix_ITU_R_709_2)
        ]
        for (key, value) in properties {
            let status = VTSessionSetProperty(session, key: key, value: value)
            if CFEqual(key, kVTCompressionPropertyKey_AverageBitRate) { bitrateSetStatus = Int(status) }
            try check(status, "set \(key)")
        }
        if ltrEnabled {
            guard #available(macOS 12.0, *) else { throw EncoderFailure(description: "LTR requires macOS12 or later") }
            let ltrStatus = VTSessionSetProperty(session, key: kVTCompressionPropertyKey_EnableLTR, value: kCFBooleanTrue)
            var reference: Unmanaged<CFTypeRef>?
            let readStatus = VTSessionCopyProperty(session, key: kVTCompressionPropertyKey_EnableLTR,
                allocator: kCFAllocatorDefault, valueOut: &reference)
            let observed = reference?.takeRetainedValue() as? NSNumber
            logJSON(["event": "ltr_configuration", "requested": true, "set_status": Int(ltrStatus),
                "read_status": Int(readStatus), "readback": readStatus == noErr ? observed as Any? ?? NSNull() : NSNull(),
                "low_latency_mode": lowLatencyMode, "profile_level": profileLevel as String])
            try check(ltrStatus, "set explicitly requested EnableLTR")
            guard readStatus == noErr, observed?.boolValue == true else {
                throw EncoderFailure(description: "LTR true readback not confirmed; refusing experimental fallback")
            }
        }
        if bitrateMode == "CBR" {
            let status = VTSessionSetProperty(session, key: "ConstantBitRate" as CFString,
                                             value: NSNumber(value: bitrate))
            bitrateSetStatus = Int(status)
            try check(status, "set required CBR target")
        } else {
            rateLimitStatus = Int(setRateLimit(session, bitrate))
        }
        // Optional API: do not mistake ignoring an unsupported property for an
        // applied limit. Check both documented low-delay candidates and report
        // their exact status. Actual hardware selection remains mandatory.
        let delayOne = VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxFrameDelayCount, value: NSNumber(value: 1))
        frameDelaySetStatus["1"] = Int(delayOne)
        if delayOne == kVTPropertyNotSupportedErr {
            let delayZero = VTSessionSetProperty(session, key: kVTCompressionPropertyKey_MaxFrameDelayCount, value: NSNumber(value: 0))
            frameDelaySetStatus["0"] = Int(delayZero)
            if delayZero != kVTPropertyNotSupportedErr { try check(delayZero, "set MaxFrameDelayCount=0") }
        } else { try check(delayOne, "set MaxFrameDelayCount=1") }
        prioritizeSpeedEvidence.configure(session)
        try check(VTCompressionSessionPrepareToEncodeFrames(session), "prepare hardware encoder")
        prioritizeSpeedEvidence.readBack(session)
        let hardwareEvidence = try hardwareVerification(session)
        var readyReport: [String: Any] = ["event": "ready", "bitrate_mode": bitrateMode,
                 "width": width, "height": height,
                 "vbr_rate_limit_status": rateLimitStatus as Any? ?? NSNull(),
                 "encoder_id": (try? sessionProperty(session, kVTCompressionPropertyKey_EncoderID)) as? String ?? "unavailable"]
        readyReport.merge(hardwareEvidence) { _, new in new }
        readyReport.merge(prioritizeSpeedEvidence.metadata) { _, new in new }
        let configuration = configurationReadback(session)
        if lowLatencyMode, let observed = configuration["low_latency_mode_readback"] as? NSNumber,
           !observed.boolValue {
            throw EncoderFailure(description: "VT low-latency readback contradicts requested true; refusing fallback")
        }
        readyReport.merge(configuration) { _, new in new }
        readyReport.merge(rateControlReadback(session, requestedTarget: bitrate,
                                              rateLimitsAttempted: bitrateMode != "CBR")) { _, new in new }
        switch pixelPoolMode {
        case .manual:
            let poolAttributes = [kCVPixelBufferPoolMinimumBufferCountKey: 3] as CFDictionary
            try check(CVPixelBufferPoolCreate(kCFAllocatorDefault, poolAttributes, pixelAttributes as CFDictionary, &pool), "create IOSurface pixel pool")
        case .session:
            guard let sessionPool = VTCompressionSessionGetPixelBufferPool(session) else {
                throw EncoderFailure(description: "VT session pixel pool unavailable; refusing manual fallback")
            }
            pool = sessionPool
        }
        guard let pool = pool else { throw EncoderFailure(description: "selected pixel pool unavailable") }
        pixelPoolEvidence = try verifiedPixelPool(pool, mode: pixelPoolMode, width: width, height: height)
        readyReport.merge(pixelPoolEvidence) { _, new in new }
        logJSON(readyReport)
        var header = emitCodec ? Data("h264".utf8) : Data()
        appendBE(UInt32(0x80000000), to: &header); appendBE(UInt32(width), to: &header); appendBE(UInt32(height), to: &header)
        try writeAll(header)
    }
    deinit { if let session = session { VTCompressionSessionInvalidate(session) } }

    private func hardwareVerification(_ session: VTCompressionSession) throws -> [String: Any] {
        var reference: Unmanaged<CFTypeRef>?
        let status = VTSessionCopyProperty(session, key: kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,
            allocator: kCFAllocatorDefault, valueOut: &reference)
        let observed = reference?.takeRetainedValue() as? NSNumber
        var evidence: [String: Any] = ["hardware_property_read_status": Int(status),
            "hardware_property_readback": status == noErr ? (observed as Any? ?? NSNull()) : NSNull(),
            "required_hardware": true]
        if status == noErr {
            guard observed?.boolValue == true else { throw EncoderFailure(description: "actual hardware readback is not true") }
            evidence["using_hardware"] = true
            evidence["hardware_verification_method"] = "session_property"
            return evidence
        }
        // The dedicated encoder may not expose this property. Only a successful
        // RequireHardware creation AND an exact selected-ID match to Apple's
        // hardware encoder registry can provide the alternate evidence path.
        guard lowLatencyMode, status == kVTPropertyNotSupportedErr,
              let encoderID = (try? sessionProperty(session, kVTCompressionPropertyKey_EncoderID)) as? String else {
            throw EncoderFailure(description: "hardware verification unavailable: OSStatus \(status)")
        }
        var listReference: CFArray?
        try check(VTCopyVideoEncoderList(nil, &listReference), "copy encoder registry for hardware verification")
        if ltrEnabled {
            let entries = (listReference as? [[String: Any]] ?? []).filter {
                ($0[kVTVideoEncoderList_CodecType as String] as? NSNumber)?.uint32Value == kCMVideoCodecType_H264
            }
            logJSON(["event": "ltr_hardware_registry", "selected_encoder_id": encoderID,
                "using_hardware_property_status": Int(status), "require_hardware_creation_succeeded": true,
                "h264_registry_entries": entries])
        }
        if ltrEnabled, ltrAllowHardwareWrapper, lowLatencyMode,
           encoderID == "com.apple.videotoolbox.videoencoder.h264.rtvc",
           let rows = listReference as? [[String: Any]],
           let selected = rows.first(where: { ($0[kVTVideoEncoderList_EncoderID as String] as? String) == encoderID }),
           selected[kVTVideoEncoderList_IsHardwareAccelerated as String] == nil {
            evidence["encoder_id"] = encoderID
            evidence["encoder_registry_hardware"] = NSNull()
            evidence["using_hardware"] = true
            evidence["hardware_verification_method"] = "required_hardware_and_documented_dedicated_low_latency_contract"
            evidence["hardware_contract_source"] = "https://developer.apple.com/videos/play/wwdc2021/10158/"
            evidence["hardware_contract_limitation"] = "RTVC wrapper has no hardware property or registry marker; hardware-only conclusion relies on explicit RequireHardware creation and Apple's low-latency API contract, not property readback."
            return evidence
        }
        guard let rows = listReference as? [[String: Any]],
              let selected = rows.first(where: { ($0[kVTVideoEncoderList_EncoderID as String] as? String) == encoderID }),
              (selected[kVTVideoEncoderList_IsHardwareAccelerated as String] as? NSNumber)?.boolValue == true else {
            throw EncoderFailure(description: "selected encoder lacks matching hardware registry evidence")
        }
        evidence["encoder_id"] = encoderID
        evidence["encoder_registry_hardware"] = true
        evidence["using_hardware"] = true
        evidence["hardware_verification_method"] = "required_hardware_and_selected_encoder_registry"
        return evidence
    }

    private func configurationReadback(_ session: VTCompressionSession) -> [String: Any] {
        var report: [String: Any] = ["low_latency_mode_requested": lowLatencyMode,
                                   "profile_level_requested": profileLevel as String]
        var properties: [(CFString, String)] = [
            (kVTCompressionPropertyKey_ProfileLevel, "profile_level"),
            (kVTCompressionPropertyKey_AllowFrameReordering, "frame_reordering"),
            (kVTCompressionPropertyKey_ExpectedFrameRate, "expected_frame_rate")
        ]
        if #available(macOS 11.3, *) {
            properties.append((kVTVideoEncoderSpecification_EnableLowLatencyRateControl, "low_latency_mode"))
        } else {
            report["low_latency_mode_read_status"] = Int(kVTPropertyNotSupportedErr)
            report["low_latency_mode_readback"] = NSNull()
        }
        for (key, prefix) in properties {
            var reference: Unmanaged<CFTypeRef>?
            let status = VTSessionCopyProperty(session, key: key, allocator: kCFAllocatorDefault, valueOut: &reference)
            let value = reference?.takeRetainedValue()
            report[prefix + "_read_status"] = Int(status)
            // A creation specification may not be exposed as a readable
            // session property. Preserve the exact error and null; accepting
            // creation is not itself a successful property readback.
            report[prefix + "_readback"] = status == noErr ? (value as Any? ?? NSNull()) : NSNull()
        }
        return report
    }

    private func rateLimits(_ target: Int) -> [NSNumber] {
        // Preserve the original 1.5x one-second cap, optionally adding a shorter
        // compressed-data window. Acceptance/readback does not prove that the
        // hardware actually respects the window; inspect emitted AUs as well.
        var limits = [NSNumber(value: Int64(target) * 3 / 16), NSNumber(value: 1.0)]
        if let bytes = burstBytes, let seconds = burstSeconds {
            limits.append(NSNumber(value: bytes)); limits.append(NSNumber(value: seconds))
        }
        return limits
    }

    private func rateControlReadback(_ session: VTCompressionSession, requestedTarget: Int,
                                     rateLimitsAttempted: Bool) -> [String: Any] {
        var report: [String: Any] = [
            "bitrate_target_requested_bps": requestedTarget,
            "bitrate_target_current_bps": currentBitrate,
            "bitrate_target_set_status": bitrateSetStatus as Any? ?? NSNull(),
            "burst_window_requested": burstBytes != nil,
            "burst_bytes_requested": burstBytes as Any? ?? NSNull(),
            "burst_seconds_requested": burstSeconds as Any? ?? NSNull(),
            "vbr_rate_limits_requested": bitrateMode != "CBR" ? rateLimits(requestedTarget) as Any : NSNull(),
            "vbr_rate_limits_set_attempted": rateLimitsAttempted,
            "vbr_rate_limit_status": rateLimitsAttempted ? rateLimitStatus as Any? ?? NSNull() : NSNull()
        ]
        let targetKey = bitrateMode == "CBR" ? "ConstantBitRate" as CFString : kVTCompressionPropertyKey_AverageBitRate
        for (key, prefix) in [(targetKey, "bitrate_target"), (kVTCompressionPropertyKey_DataRateLimits, "vbr_rate_limits")] {
            var reference: Unmanaged<CFTypeRef>?
            let status = VTSessionCopyProperty(session, key: key, allocator: kCFAllocatorDefault, valueOut: &reference)
            let value = reference?.takeRetainedValue()
            report[prefix + "_read_status"] = Int(status)
            report[prefix + "_readback"] = status == noErr ? (value as Any? ?? NSNull()) : NSNull()
        }
        return report
    }

    private func setRateLimit(_ session: VTCompressionSession, _ target: Int) -> OSStatus {
        return VTSessionSetProperty(session, key: kVTCompressionPropertyKey_DataRateLimits,
                                    value: rateLimits(target) as CFArray)
    }

    func updateBitrate(_ value: Int, sequence: UInt64) -> Bool {
        var status: OSStatus = kVTInvalidSessionErr
        var rateLimitsAttempted = false
        if let session = session, (500_000...100_000_000).contains(value) {
            let key = bitrateMode == "CBR" ? "ConstantBitRate" as CFString : kVTCompressionPropertyKey_AverageBitRate
            status = VTSessionSetProperty(session, key: key, value: NSNumber(value: value))
            if status == noErr { currentBitrate = value }
            if status == noErr && bitrateMode != "CBR" {
                rateLimitsAttempted = true; rateLimitStatus = Int(setRateLimit(session, value))
            }
        }
        bitrateSetStatus = Int(status)
        var report: [String: Any] = ["event": "bitrate", "sequence": sequence,
                                    "accepted_bitrate": status == noErr ? value : 0, "status": Int(status)]
        if let session = session {
            report.merge(rateControlReadback(session, requestedTarget: value,
                                             rateLimitsAttempted: rateLimitsAttempted)) { _, new in new }
        }
        logJSON(report)
        return status == noErr
    }

    func ltrControl(type: UInt64, value: UInt64, sequence: UInt64) throws {
        guard ltrEnabled, #available(macOS 12.0, *) else { throw EncoderFailure(description: "LTR control requires explicit enable-ltr true") }
        if type == 3 {
            guard value <= UInt64(Int64.max) else { throw EncoderFailure(description: "invalid LTR token range") }
            let token = Int64(value)
            lock.lock(); let emitted = emittedLTRTokens.contains(token); lock.unlock()
            guard emitted else { throw EncoderFailure(description: "ACK token was not emitted by this encoder session") }
            guard acknowledgedLTRTokens.count < 64 else { throw EncoderFailure(description: "too many pending LTR ACKs") }
            acknowledgedLTRTokens.append(NSNumber(value: token))
        } else if type == 4, value == 0 {
            forceLTRRefresh = true
        } else { throw EncoderFailure(description: "invalid LTR control") }
        logJSON(["event": "ltr_control", "type": type, "value": value, "sequence": sequence,
                 "ack_scope": "caller_reports_receiver_acceptance_not_verified_by_encoder"])
    }

    func submit(_ rgba: Data, pts: UInt64, rawReadyNS: UInt64, traceFields: [String: Any]? = nil) throws {
        if let prior = previousPTS, pts <= prior { throw EncoderFailure(description: "input PTS must increase") }
        if pts >= (UInt64(1) << 61) { throw EncoderFailure(description: "PTS uses protocol flag bits") }
        lock.lock(); let failure = errorMessage; lock.unlock()
        if let failure = failure { throw EncoderFailure(description: failure) }
        let slotBeginNS = captureTrace != nil ? traceClockNS() : 0
        guard slots.wait(timeout: .now() + 2) == .success else { throw EncoderFailure(description: "encoder backlog exceeded 3 frames for 2 seconds") }
        let slotEndNS = captureTrace != nil ? traceClockNS() : 0
        var slotTransferred = false
        defer { if !slotTransferred { slots.signal() } }
        guard let session = session, let pool = pool else { throw EncoderFailure(description: "encoder unavailable") }
        let conversionStart = nowNS()
        let conversionBeginTraceNS = captureTrace != nil ? traceClockNS() : 0
        var buffer: CVPixelBuffer?
        try check(CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &buffer), "allocate pooled BGRA")
        guard let pixel = buffer else { throw EncoderFailure(description: "missing pixel buffer") }
        try verifiedBGRABuffer(pixel, width: width, height: height)
        try check(CVPixelBufferLockBaseAddress(pixel, []), "lock BGRA")
        let conversionStatus: vImage_Error = rgba.withUnsafeBytes { source in
            var input = vImage_Buffer(data: UnsafeMutableRawPointer(mutating: source.baseAddress!),
                height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: width * 4)
            var output = vImage_Buffer(data: CVPixelBufferGetBaseAddress(pixel)!,
                height: vImagePixelCount(height), width: vImagePixelCount(width), rowBytes: CVPixelBufferGetBytesPerRow(pixel))
            let map: [UInt8] = [2, 1, 0, 3]
            return map.withUnsafeBufferPointer { vImagePermuteChannels_ARGB8888(&input, &output, $0.baseAddress!, vImage_Flags(kvImageNoFlags)) }
        }
        CVPixelBufferUnlockBaseAddress(pixel, [])
        if conversionStatus != kvImageNoError { throw EncoderFailure(description: "vImage RGBA-to-BGRA: \(conversionStatus)") }
        let submitNS = nowNS()
        let conversionEndTraceNS = captureTrace != nil ? traceClockNS() : 0
        lock.lock()
        submitted += 1; pending += 1; pendingPeak = max(pendingPeak, pending)
        if conversionMS.count < 3600 { conversionMS.append(ms(conversionStart, submitNS)) }
        let unixUS = UInt64(Date().timeIntervalSince1970 * 1e6)
        if pts > 1_000_000_000_000 && unixUS >= pts && unixUS - pts < 5_000_000 {
            if captureAgeMS.count < 3600 { captureAgeMS.append(Double(unixUS - pts) / 1000) }
        }
        lock.unlock()
        slotTransferred = true
        var options: [CFString: Any] = [:]
        if forceKeyframe { options[kVTEncodeFrameOptionKey_ForceKeyFrame] = kCFBooleanTrue! }
        forceKeyframe = false
        if ltrEnabled, #available(macOS 12.0, *) {
            if !acknowledgedLTRTokens.isEmpty { options[kVTEncodeFrameOptionKey_AcknowledgedLTRTokens] = acknowledgedLTRTokens as CFArray }
            if forceLTRRefresh { options[kVTEncodeFrameOptionKey_ForceLTRRefresh] = kCFBooleanTrue! }
            if !acknowledgedLTRTokens.isEmpty || forceLTRRefresh {
                logJSON(["event": "ltr_frame_request", "pts_us": pts,
                    "ack_tokens": acknowledgedLTRTokens, "force_ltr_refresh": forceLTRRefresh])
            }
            acknowledgedLTRTokens.removeAll(); forceLTRRefresh = false
        }
        let frameProperties = options.isEmpty ? nil : options as CFDictionary
        var trace = traceFields
        if captureTrace != nil {
            trace = (trace ?? [:]).merging(["slot_wait_begin_ns": slotBeginNS, "slot_wait_end_ns": slotEndNS,
                "pixel_conversion_begin_ns": conversionBeginTraceNS, "pixel_conversion_end_ns": conversionEndTraceNS,
                "vt_submit_ns": traceClockNS()]) { _, value in value }
        }
        let ticket = Unmanaged.passRetained(FrameContext(pts, rawReadyNS, submitNS, pixel, traceFields: trace))
        let status = VTCompressionSessionEncodeFrame(session, imageBuffer: pixel,
            presentationTimeStamp: CMTime(value: Int64(pts), timescale: 1_000_000),
            duration: CMTime(value: 1, timescale: Int32(fps)), frameProperties: frameProperties,
            sourceFrameRefcon: ticket.toOpaque(), infoFlagsOut: nil)
        if let captureTrace = captureTrace {
            captureTrace.record(["event": "vt_submit_return", "source_pts_us": pts,
                "vt_submit_return_ns": traceClockNS(), "vt_submit_status": Int(status)])
        }
        if status != noErr { deliver(ticket.takeRetainedValue(), status, [], nil) }
        previousPTS = pts
        try check(status, "submit hardware frame")
    }

    func packet(pts: UInt64, payload: Data) throws {
        guard payload.count <= 8 * 1024 * 1024 else { throw EncoderFailure(description: "encoded packet exceeds client 8 MiB limit") }
        var data = Data(); appendBE(pts, to: &data); appendBE(UInt32(payload.count), to: &data); data.append(payload)
        try writeAll(data)
    }
    func deliver(_ frame: FrameContext, _ status: OSStatus, _ flags: VTEncodeInfoFlags, _ sample: CMSampleBuffer?) {
        let callbackNS = nowNS()
        var trace = frame.traceFields
        if captureTrace != nil {
            trace?["event"] = "vt_frame"
            trace?["source_pts_us"] = frame.pts
            trace?["vt_callback_ns"] = traceClockNS()
            trace?["vt_status"] = Int(status)
            trace?["frame_dropped"] = flags.contains(.frameDropped)
        }
        defer { if let trace = trace { captureTrace?.record(trace) } }
        defer { lock.lock(); pending -= 1; lock.unlock(); slots.signal() }
        do {
            try check(status, "hardware output callback")
            guard !flags.contains(.frameDropped), let sample = sample, CMSampleBufferDataIsReady(sample) else {
                lock.lock(); dropped += 1; lock.unlock(); return
            }
            guard let format = CMSampleBufferGetFormatDescription(sample), let block = CMSampleBufferGetDataBuffer(sample) else {
                throw EncoderFailure(description: "missing compressed sample data")
            }
            let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[String: Any]]
            let keyframe = !(attachments?.first?[kCMSampleAttachmentKey_NotSync as String] as? Bool ?? false)
            var configuration = Data(), nalLength = Int32(4), parameterCount = 0
            var parameterIndex = 0
            repeat {
                var pointer: UnsafePointer<UInt8>?, length = 0
                try check(CMVideoFormatDescriptionGetH264ParameterSetAtIndex(format, parameterSetIndex: parameterIndex,
                    parameterSetPointerOut: &pointer, parameterSetSizeOut: &length,
                    parameterSetCountOut: &parameterCount, nalUnitHeaderLengthOut: &nalLength), "read H264 parameter set")
                guard let pointer = pointer else { throw EncoderFailure(description: "empty H264 parameter set") }
                configuration.append(contentsOf: [0, 0, 0, 1]); configuration.append(pointer, count: length)
                parameterIndex += 1
            } while parameterIndex < parameterCount
            guard [1, 2, 4].contains(Int(nalLength)) else { throw EncoderFailure(description: "invalid AVC NAL length field") }
            let compressedLength = CMBlockBufferGetDataLength(block)
            var avcc = Data(count: compressedLength)
            try check(avcc.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: compressedLength, destination: $0.baseAddress!) }, "copy encoded AVC")
            var annexB = Data(), offset = 0
            while offset + Int(nalLength) <= avcc.count {
                let length = Int(unsignedBE(avcc, offset, Int(nalLength))); offset += Int(nalLength)
                guard length > 0, length <= avcc.count - offset else { throw EncoderFailure(description: "truncated AVC NAL") }
                annexB.append(contentsOf: [0, 0, 0, 1]); annexB.append(avcc[offset..<(offset + length)]); offset += length
            }
            guard offset == avcc.count, !annexB.isEmpty else { throw EncoderFailure(description: "invalid AVC packet tail") }
            if ltrEnabled, #available(macOS 12.0, *) {
                let token = attachments?.first?[kVTSampleAttachmentKey_RequireLTRAcknowledgementToken as String] as? NSNumber
                    ?? CMGetAttachment(sample, key: kVTSampleAttachmentKey_RequireLTRAcknowledgementToken, attachmentModeOut: nil) as? NSNumber
                if let token = token {
                    lock.lock()
                    let tooMany = emittedLTRTokens.count >= 4096 && !emittedLTRTokens.contains(token.int64Value)
                    if !tooMany { emittedLTRTokens.insert(token.int64Value) }
                    lock.unlock()
                    if tooMany { throw EncoderFailure(description: "LTR experiment token history reached 4096; restart isolated probe") }
                }
                logJSON(["event": "ltr_output", "pts_us": frame.pts, "au_bytes": annexB.count,
                    "keyframe": keyframe, "ack_token": token as Any? ?? NSNull(),
                    "ack_scope": "token_emitted_not_receiver_confirmed"])
            }
            trace?["stdout_lock_wait_begin_ns"] = captureTrace != nil ? traceClockNS() : 0
            outputLock.lock(); defer { outputLock.unlock() }
            trace?["stdout_lock_acquired_ns"] = captureTrace != nil ? traceClockNS() : 0
            let writeStart = nowNS()
            if configuration != previousConfig {
                try packet(pts: UInt64(1) << 62, payload: configuration); previousConfig = configuration
            }
            trace?["stdout_write_begin_ns"] = captureTrace != nil ? traceClockNS() : 0
            try packet(pts: frame.pts | (keyframe ? UInt64(1) << 61 : 0), payload: annexB)
            trace?["stdout_write_end_ns"] = captureTrace != nil ? traceClockNS() : 0
            trace?["au_bytes"] = annexB.count
            trace?["keyframe"] = keyframe
            let writeEnd = nowNS()
            lock.lock()
            encoded += 1; if keyframe { keyframes += 1 }; bytesOutput += annexB.count
            if encodeMS.count < 3600 { encodeMS.append(ms(frame.submitNS, callbackNS)); fullMS.append(ms(frame.rawReadyNS, callbackNS)) }
            if writeMS.count < 3600 { writeMS.append(ms(writeStart, writeEnd)); outputTimesNS.append(callbackNS) }
            lock.unlock()
        } catch {
            lock.lock(); dropped += 1; if errorMessage == nil { errorMessage = String(describing: error) }; lock.unlock()
        }
    }

    func finish(reason: String) throws {
        guard let session = session else { return }
        let watchdog = DispatchWorkItem {
            logJSON(["probe": "emulator-hardware-rgba-v1", "error": "native drain exceeded 8 seconds"])
            _exit(2)
        }
        DispatchQueue.global().asyncAfter(deadline: .now() + 8, execute: watchdog)
        defer { watchdog.cancel() }
        let flushStart = nowNS()
        try check(VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid), "drain encoder")
        let finishedNS = nowNS()
        let hardwareEvidence = try hardwareVerification(session)
        let encoderID = (try? sessionProperty(session, kVTCompressionPropertyKey_EncoderID)) as? String ?? "unavailable"
        let noB = (try? sessionProperty(session, kVTCompressionPropertyKey_AllowFrameReordering)) as? NSNumber
        var frameDelayReference: Unmanaged<CFTypeRef>?
        let frameDelayReadStatus = VTSessionCopyProperty(session, key: kVTCompressionPropertyKey_MaxFrameDelayCount,
            allocator: kCFAllocatorDefault, valueOut: &frameDelayReference)
        let frameDelayLimit = frameDelayReadStatus == noErr ? frameDelayReference?.takeRetainedValue() as? NSNumber : nil
        // VT may return a default/cached value even after rejecting the setter.
        // Preserve that raw observation without claiming the requested limit
        // was applied. A confirmed count requires a successful matching set.
        let acceptedDelay = frameDelaySetStatus.first(where: { $0.value == Int(noErr) }).flatMap { Int($0.key) }
        let confirmedDelay = acceptedDelay != nil && acceptedDelay == frameDelayLimit?.intValue ? acceptedDelay : nil
        lock.lock(); defer { lock.unlock() }
        let seconds = Double(finishedNS - startedNS) / 1e9
        var gaps: [Double] = []
        if outputTimesNS.count >= 2 { for i in 1..<outputTimesNS.count { gaps.append(ms(outputTimesNS[i-1], outputTimesNS[i])) } }
        var report: [String: Any] = [
            "probe": "emulator-hardware-rgba-v1", "finish_reason": reason,
            "width": width, "height": height, "fps_expected": fps, "bitrate_target_bps": bitrate,
            "required_hardware": true, "encoder_id": encoderID,
            "frame_reordering_readback": noB?.boolValue as Any? ?? NSNull(),
            "max_frame_delay_set_status": frameDelaySetStatus,
            "max_frame_delay_read_status": Int(frameDelayReadStatus),
            "max_frame_delay_raw_readback": frameDelayLimit?.intValue as Any? ?? NSNull(),
            "max_frame_delay_count_readback": confirmedDelay as Any? ?? NSNull(),
            "max_frame_delay_applied": confirmedDelay != nil,
            "input_frames": submitted, "output_frames": encoded, "dropped_or_failed": dropped,
            "pending_at_end": pending, "pending_peak": pendingPeak, "keyframes": keyframes,
            "bitrate_mode": bitrateMode, "distribution_sample_limit": 3600,
            "vbr_rate_limit_status": rateLimitStatus as Any? ?? NSNull(),
            "elapsed_seconds": seconds, "output_fps": Double(encoded) / seconds,
            "output_bitrate_bps": Double(bytesOutput) * 8 / seconds,
            "vimage_and_pool_ms": distribution(conversionMS), "submit_to_callback_ms": distribution(encodeMS),
            "complete_rgba_to_callback_ms": distribution(fullMS), "stdout_write_ms": distribution(writeMS),
            "output_callback_gap_ms": distribution(gaps), "host_capture_timestamp_age_at_submit_ms": distribution(captureAgeMS),
            "final_drain_ms": ms(flushStart, finishedNS),
            "process_cpu_percent_of_one_core": 100 * (cpuSeconds() - cpuStart) / seconds,
            "scope": "complete_rgba_to_callback_ms starts after stdin delivered the complete RGBA payload. It measures pool/conversion/VT work and excludes gRPC capture delivery, RGBA pipe delivery, network transport, phone decode and display. Capture timestamp age is a separate estimated upstream observation."
        ]
        report.merge(hardwareEvidence) { _, new in new }
        report.merge(pixelPoolEvidence) { _, new in new }
        prioritizeSpeedEvidence.readBack(session)
        report.merge(prioritizeSpeedEvidence.metadata) { _, new in new }
        report.merge(configurationReadback(session)) { _, new in new }
        if let errorMessage = errorMessage { report["error"] = errorMessage }
        logJSON(report)
        if let errorMessage = errorMessage { throw EncoderFailure(description: errorMessage) }
    }
}

signal(SIGPIPE, SIG_IGN)
// Nonblocking writes plus poll make pipe/socket output deadlines effective even
// when a slow reader consumes only part of a packet. Input remains poll-bounded.
let stdoutFlags = fcntl(STDOUT_FILENO, F_GETFL)
if stdoutFlags >= 0 { _ = fcntl(STDOUT_FILENO, F_SETFL, stdoutFlags | O_NONBLOCK) }
var fps = 30, bitrate = 4_000_000, maxFrames = 600, maxSeconds = 30.0, idleMS: Int32 = 10_000
var service = false, bitrateMode = "VBR", lowLatencyMode = false, enableLTR = false, ltrAllowHardwareWrapper = false
var burstBytes: Int?, burstSeconds: Double?
var prioritizeSpeed: Bool?
var pixelPoolMode: PixelPoolMode = .manual
var tracePath: String?, traceWriter: BoundedCaptureTrace?
var arguments = Array(CommandLine.arguments.dropFirst()), encoder: HardwareEncoder?
do {
    while !arguments.isEmpty {
        let key = arguments.removeFirst(); guard !arguments.isEmpty else { throw EncoderFailure(description: "missing value for \(key)") }
        let value = arguments.removeFirst()
        switch key {
        case "--service": guard value == "true" else { throw EncoderFailure(description: "service expects true") }; service = true
        case "--trace": tracePath = value
        case "--mode": guard value == "CBR" || value == "VBR" else { throw EncoderFailure(description: "mode expects CBR or VBR") }; bitrateMode = value
        case "--low-latency-mode":
            guard value == "true" || value == "false" else { throw EncoderFailure(description: "low-latency-mode expects true or false") }
            lowLatencyMode = value == "true"
        case "--prioritize-speed":
            guard value == "true" || value == "false" else { throw EncoderFailure(description: "prioritize-speed expects true or false") }
            prioritizeSpeed = value == "true"
        case "--pixel-pool":
            guard let mode = PixelPoolMode(rawValue: value) else {
                throw EncoderFailure(description: "pixel-pool expects manual or session")
            }
            pixelPoolMode = mode
        case "--enable-ltr":
            guard value == "true" || value == "false" else { throw EncoderFailure(description: "enable-ltr expects true or false") }
            enableLTR = value == "true"
        case "--ltr-allow-hardware-wrapper":
            guard value == "true" || value == "false" else { throw EncoderFailure(description: "ltr-allow-hardware-wrapper expects true or false") }
            ltrAllowHardwareWrapper = value == "true"
        case "--burst-bytes":
            guard let n = Int(value), (32768...2_000_000).contains(n) else { throw EncoderFailure(description: "burst-bytes must be 32768...2000000") }
            burstBytes = n
        case "--burst-seconds":
            guard let n = Double(value), n.isFinite, (0.02...1.0).contains(n) else { throw EncoderFailure(description: "burst-seconds must be finite and 0.02...1.0") }
            burstSeconds = n
        case "--fps": guard let n = Int(value), (1...120).contains(n) else { throw EncoderFailure(description: "fps must be 1...120") }; fps = n
        case "--bitrate": guard let n = Int(value), (100_000...100_000_000).contains(n) else { throw EncoderFailure(description: "invalid bitrate") }; bitrate = n
        case "--max-frames": guard let n = Int(value), (1...3600).contains(n) else { throw EncoderFailure(description: "max-frames must be 1...3600") }; maxFrames = n
        case "--max-seconds": guard let n = Double(value), n >= 1, n <= 120 else { throw EncoderFailure(description: "max-seconds must be 1...120") }; maxSeconds = n
        case "--idle-seconds": guard let n = Double(value), n >= 1, n <= 30 else { throw EncoderFailure(description: "idle-seconds must be 1...30") }; idleMS = Int32(n * 1000)
        default: throw EncoderFailure(description: "unknown option \(key)")
        }
    }
    try validateBurstLimit(bytes: burstBytes, seconds: burstSeconds, mode: bitrateMode)
    guard !ltrAllowHardwareWrapper || enableLTR && lowLatencyMode else {
        throw EncoderFailure(description: "LTR hardware wrapper experiment requires enable-ltr and low-latency-mode true")
    }
    if let tracePath = tracePath { traceWriter = try BoundedCaptureTrace(path: tracePath) }
    let started = nowNS(), inputDeadline = service ? UInt64.max : started + UInt64(maxSeconds * 1e9)
    var frames = 0, reason = "EOF", emittedCodec = false
    while service || frames < maxFrames {
        if !service && Double(nowNS() - started) / 1e9 >= maxSeconds { reason = "time_limit"; break }
        let headerBeginNS = traceWriter != nil ? traceClockNS() : 0
        guard let header = try readExact(20, idleMS: idleMS, deadlineNS: inputDeadline, cleanEOF: true) else { break }
        let headerCompleteNS = traceWriter != nil ? traceClockNS() : 0
        let width = Int(unsignedBE(header, 0, 4)), height = Int(unsignedBE(header, 4, 4))
        let pts = unsignedBE(header, 8, 8), length = Int(unsignedBE(header, 16, 4))
        if service && width == 0 && height == 0 && length == 8 {
            guard let command = try readExact(8, idleMS: idleMS, deadlineNS: inputDeadline) else { break }
            let type = unsignedBE(command, 0, 4), value = Int(unsignedBE(command, 4, 4))
            if type == 1, encoder?.updateBitrate(value, sequence: pts) == true { bitrate = value }
            else if type == 2 { encoder?.forceKeyframe = true }
            else { throw EncoderFailure(description: "invalid local encoder control") }
            continue
        }
        if service && enableLTR && width == 0 && height == 0 && length == 12 {
            guard let command = try readExact(12, idleMS: idleMS, deadlineNS: inputDeadline), let current = encoder else {
                throw EncoderFailure(description: "LTR command requires an initialized encoder")
            }
            try current.ltrControl(type: unsignedBE(command, 0, 4), value: unsignedBE(command, 4, 8), sequence: pts)
            continue
        }
        guard width >= 2, height >= 2, width <= 8192, height <= 8192, width % 2 == 0, height % 2 == 0,
              length == width * height * 4, length <= 64 * 1024 * 1024 else { throw EncoderFailure(description: "invalid complete RGBA frame dimensions/length") }
        let rawReadBeginNS = traceWriter != nil ? traceClockNS() : 0
        guard let rgba = try readExact(length, idleMS: idleMS, deadlineNS: inputDeadline) else { throw EncoderFailure(description: "missing RGBA") }
        let ready = nowNS()
        var traceFields: [String: Any]?
        if let traceWriter = traceWriter {
            traceFields = ["event": "raw_read", "native_input_seq": frames + 1, "source_pts_us": pts,
                "header_read_begin_ns": headerBeginNS, "header_read_complete_ns": headerCompleteNS,
                "raw_read_begin_ns": rawReadBeginNS, "raw_read_complete_ns": traceClockNS(),
                "width": width, "height": height, "raw_bytes": length]
            traceWriter.record(traceFields!)
        }
        if service, let current = encoder, width != current.width || height != current.height {
            try current.finish(reason: "resize"); encoder = nil
        }
        if encoder == nil {
            encoder = try HardwareEncoder(width: width, height: height, fps: fps, bitrate: bitrate,
                                          bitrateMode: bitrateMode, emitCodec: !emittedCodec, lowLatencyMode: lowLatencyMode,
                                          burstBytes: burstBytes, burstSeconds: burstSeconds, enableLTR: enableLTR,
                                          ltrAllowHardwareWrapper: ltrAllowHardwareWrapper, captureTrace: traceWriter,
                                          prioritizeSpeed: prioritizeSpeed, pixelPoolMode: pixelPoolMode)
            emittedCodec = true
        }
        guard let current = encoder, width == current.width, height == current.height else { throw EncoderFailure(description: "resolution changes require a new prototype process") }
        try current.submit(rgba, pts: pts, rawReadyNS: ready, traceFields: traceFields); frames += 1
    }
    if !service && frames == maxFrames { reason = "frame_limit" }
    if let encoder = encoder { try encoder.finish(reason: reason) }
    else { logJSON(["probe": "emulator-hardware-rgba-v1", "input_frames": 0, "finish_reason": reason,
                    "low_latency_mode_requested": lowLatencyMode, "burst_window_requested": burstBytes != nil,
                    "prioritize_speed_requested": prioritizeSpeed as Any? ?? NSNull(),
                    "pixel_pool_mode": pixelPoolMode.rawValue,
                    "burst_bytes_requested": burstBytes as Any? ?? NSNull(),
                    "burst_seconds_requested": burstSeconds as Any? ?? NSNull()]) }
    traceWriter?.finish()
} catch {
    logJSON(["probe": "emulator-hardware-rgba-v1", "error": String(describing: error),
             "low_latency_mode_requested": lowLatencyMode, "burst_window_requested": burstBytes != nil,
             "prioritize_speed_requested": prioritizeSpeed as Any? ?? NSNull(),
             "pixel_pool_mode": pixelPoolMode.rawValue,
             "burst_bytes_requested": burstBytes as Any? ?? NSNull(),
             "burst_seconds_requested": burstSeconds as Any? ?? NSNull()])
    // In-flight frames are bounded at 3 and stdout callbacks time out. Attempt
    // cleanup without allowing a wedged native drain to hang an isolated probe.
    DispatchQueue.global().asyncAfter(deadline: .now() + 8) { _exit(1) }
    if let encoder = encoder { try? encoder.finish(reason: "error") }
    traceWriter?.finish()
    exit(1)
}
