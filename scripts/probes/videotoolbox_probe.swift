// Standalone, synthetic host-encoder probe. Does not connect to an Android guest,
// access real screen contents, write video, or change running services.
// Build: xcrun swiftc -O -module-cache-path /private/tmp/huoguo-swift-cache \
//   scripts/probes/videotoolbox_probe.swift -o /private/tmp/huoguo-vt-probe
// Run: /private/tmp/huoguo-vt-probe --seconds 6 --warmup 1 --fps 30,60 --low-latency 0
// Compare with --low-latency 1. Some low-latency encoders do not expose the
// UsingHardwareAcceleratedVideoEncoder readback; this probe fails explicitly.
// Latency here is submit-to-VT-output-callback, NOT end-to-end remote latency.

import Foundation
import CoreVideo
import CoreMedia
import VideoToolbox
import Darwin

struct ProbeFailure: Error, CustomStringConvertible {
    let description: String
}

func checked(_ status: OSStatus, _ operation: String) throws {
    if status != noErr { throw ProbeFailure(description: "\(operation): OSStatus \(status)") }
}

func monotonicNS() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
func elapsedMS(_ start: UInt64, _ end: UInt64) -> Double {
    return Double(end >= start ? end - start : 0) / 1_000_000
}
func cpuSeconds() -> Double {
    var usage = rusage()
    guard getrusage(RUSAGE_SELF, &usage) == 0 else { return 0 }
    return Double(usage.ru_utime.tv_sec + usage.ru_stime.tv_sec)
        + Double(usage.ru_utime.tv_usec + usage.ru_stime.tv_usec) / 1_000_000
}
func summary(_ values: [Double]) -> [String: Any] {
    let sorted = values.sorted()
    guard !sorted.isEmpty else { return ["count": 0] }
    func p(_ fraction: Double) -> Double {
        return sorted[min(sorted.count - 1, max(0, Int(ceil(Double(sorted.count) * fraction)) - 1))]
    }
    return ["count": sorted.count, "p50": p(0.50), "p95": p(0.95), "max": sorted.last!]
}

final class FrameTicket {
    let submittedNS: UInt64
    let measured: Bool
    init(_ submittedNS: UInt64, _ measured: Bool) {
        self.submittedNS = submittedNS
        self.measured = measured
    }
}

final class ProbeMetrics {
    let lock = NSLock()
    var latencyMS: [Double] = []
    var outputTimesNS: [UInt64] = []
    var outputBytes = 0
    var keyframes = 0
    var callbacks = 0
    var pending = 0
    var pendingPeak = 0
    var dropped = 0
    var callbackErrors: [Int32: Int] = [:]
    func submitted() {
        lock.lock(); defer { lock.unlock() }
        pending += 1
        pendingPeak = max(pendingPeak, pending)
    }
    func returned(_ ticket: FrameTicket, _ status: OSStatus,
                  _ flags: VTEncodeInfoFlags, _ sample: CMSampleBuffer?) {
        let now = monotonicNS()
        lock.lock(); defer { lock.unlock() }
        pending = max(0, pending - 1)
        if !ticket.measured { return }
        callbacks += 1
        guard status == noErr, let sample = sample, CMSampleBufferDataIsReady(sample),
              !flags.contains(.frameDropped) else {
            if status != noErr { callbackErrors[status, default: 0] += 1 }
            dropped += 1
            return
        }
        latencyMS.append(elapsedMS(ticket.submittedNS, now))
        outputTimesNS.append(now)
        outputBytes += CMSampleBufferGetTotalSampleSize(sample)
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false)
            as? [[String: Any]]
        let notSync = attachments?.first?[kCMSampleAttachmentKey_NotSync as String] as? Bool ?? false
        if !notSync { keyframes += 1 }
    }
}

let encoded: VTCompressionOutputCallback = { context, source, status, flags, sample in
    guard let context = context, let source = source else { return }
    let metrics = Unmanaged<ProbeMetrics>.fromOpaque(context).takeUnretainedValue()
    let ticket = Unmanaged<FrameTicket>.fromOpaque(source).takeRetainedValue()
    metrics.returned(ticket, status, flags, sample)
}

func fillNV12(_ pixel: CVPixelBuffer, _ frame: Int) throws {
    try checked(CVPixelBufferLockBaseAddress(pixel, []), "lock pixel buffer")
    defer { CVPixelBufferUnlockBaseAddress(pixel, []) }
    guard let y = CVPixelBufferGetBaseAddressOfPlane(pixel, 0),
          let uv = CVPixelBufferGetBaseAddressOfPlane(pixel, 1) else {
        throw ProbeFailure(description: "NV12 pixel buffer has no plane addresses")
    }
    let width = CVPixelBufferGetWidthOfPlane(pixel, 0)
    let height = CVPixelBufferGetHeightOfPlane(pixel, 0)
    let yStride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 0)
    let uvStride = CVPixelBufferGetBytesPerRowOfPlane(pixel, 1)
    // Moving luma bands and a moving vertical stripe: frame-driven, compressible motion.
    // This is a capability/latency probe, not a model of a real short-video workload.
    let stripeStart = (frame * 7) % max(1, width - 54)
    for row in 0..<height {
        let luma = Int32(32 + ((row / 40 + frame / 3) % 8) * 24)
        memset(y.advanced(by: row * yStride), luma, width)
        memset(y.advanced(by: row * yStride + stripeStart), 225, 54)
    }
    for row in 0..<CVPixelBufferGetHeightOfPlane(pixel, 1) {
        memset(uv.advanced(by: row * uvStride), 128, width)
    }
}

func property(_ session: VTCompressionSession, _ key: CFString) -> Any? {
    // The C API writes a retained CF object through a void pointer. Keep the
    // raw CF reference unmanaged until consuming it, rather than passing a
    // Swift strong Optional<AnyObject> slot as raw memory.
    var value: Unmanaged<CFTypeRef>?
    let status = VTSessionCopyProperty(session, key: key, allocator: kCFAllocatorDefault,
                                      valueOut: &value)
    if status != noErr {
        fputs("VTSessionCopyProperty \(key): OSStatus \(status)\n", stderr)
        return nil
    }
    return value?.takeRetainedValue()
}

func runProbe(fps: Int, seconds: Double, warmup: Double, bitrate: Int, lowLatency: Bool) throws -> [String: Any] {
    let width = 540, height = 1200
    let metrics = ProbeMetrics()
    var spec: [CFString: Any] = [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: kCFBooleanTrue!]
    if lowLatency { spec[kVTVideoEncoderSpecification_EnableLowLatencyRateControl] = kCFBooleanTrue! }
    let attributes: [CFString: Any] = [
        kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange,
        kCVPixelBufferWidthKey: width,
        kCVPixelBufferHeightKey: height,
        kCVPixelBufferIOSurfacePropertiesKey: [:]
    ]
    var created: VTCompressionSession?
    try checked(VTCompressionSessionCreate(allocator: kCFAllocatorDefault,
        width: Int32(width), height: Int32(height), codecType: kCMVideoCodecType_H264,
        encoderSpecification: spec as CFDictionary, imageBufferAttributes: attributes as CFDictionary,
        compressedDataAllocator: nil, outputCallback: encoded,
        refcon: Unmanaged.passUnretained(metrics).toOpaque(),
        compressionSessionOut: &created), "create required hardware H264 encoder")
    guard let session = created else { throw ProbeFailure(description: "encoder session missing") }
    defer { VTCompressionSessionInvalidate(session) }
    let properties: [(CFString, CFTypeRef)] = [
        (kVTCompressionPropertyKey_RealTime, kCFBooleanTrue),
        (kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse),
        (kVTCompressionPropertyKey_ProfileLevel, kVTProfileLevel_H264_Baseline_AutoLevel),
        (kVTCompressionPropertyKey_ExpectedFrameRate, NSNumber(value: fps)),
        (kVTCompressionPropertyKey_AverageBitRate, NSNumber(value: bitrate)),
        (kVTCompressionPropertyKey_MaxKeyFrameInterval, NSNumber(value: fps * 2)),
        (kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, NSNumber(value: 2.0))
    ]
    for (key, value) in properties {
        try checked(VTSessionSetProperty(session, key: key, value: value), "set \(key)")
    }
    try checked(VTCompressionSessionPrepareToEncodeFrames(session), "prepare encoder")
    // Some encoders initialize lazily: read actual hardware usage after frames
    // have been encoded, rather than treating a pre-first-frame false as final.
    guard let pool = VTCompressionSessionGetPixelBufferPool(session) else {
        throw ProbeFailure(description: "encoder pixel buffer pool missing")
    }
    let warmupFrames = Int(ceil(warmup * Double(fps)))
    let measuredFrames = max(1, Int(ceil(seconds * Double(fps))))
    let totalFrames = warmupFrames + measuredFrames
    let intervalNS = UInt64(1_000_000_000 / fps)
    let startedNS = monotonicNS()
    var measuredStartNS: UInt64 = startedNS
    var measuredCPUStart = cpuSeconds()
    var prepMS: [Double] = [], submitMS: [Double] = [], scheduleLateMS: [Double] = []
    var submitErrors: [Int32: Int] = [:]
    var attempted = 0
    for index in 0..<totalFrames {
        let targetNS = startedNS + UInt64(index) * intervalNS
        let beforeSleepNS = monotonicNS()
        if targetNS > beforeSleepNS {
            Thread.sleep(forTimeInterval: Double(targetNS - beforeSleepNS) / 1_000_000_000)
        }
        let measured = index >= warmupFrames
        if index == warmupFrames {
            measuredStartNS = monotonicNS(); measuredCPUStart = cpuSeconds()
        }
        let prepStartNS = monotonicNS()
        var buffer: CVPixelBuffer?
        try checked(CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &buffer), "pool allocation")
        guard let pixel = buffer else { throw ProbeFailure(description: "pool returned nil") }
        try fillNV12(pixel, index)
        let submitStartNS = monotonicNS()
        let ticket = Unmanaged.passRetained(FrameTicket(submitStartNS, measured))
        metrics.submitted()
        let status = VTCompressionSessionEncodeFrame(session, imageBuffer: pixel,
            presentationTimeStamp: CMTime(value: Int64(index), timescale: Int32(fps)),
            duration: CMTime(value: 1, timescale: Int32(fps)), frameProperties: nil,
            sourceFrameRefcon: ticket.toOpaque(), infoFlagsOut: nil)
        let submitEndNS = monotonicNS()
        if status != noErr {
            // VT does not invoke the output callback when submission fails.
            let failed = ticket.takeRetainedValue()
            metrics.returned(failed, status, [], nil)
            if measured { submitErrors[status, default: 0] += 1 }
        }
        if measured {
            attempted += 1
            prepMS.append(elapsedMS(prepStartNS, submitStartNS))
            submitMS.append(elapsedMS(submitStartNS, submitEndNS))
            scheduleLateMS.append(elapsedMS(targetNS, submitStartNS))
        }
    }
    let endTargetNS = startedNS + UInt64(totalFrames) * intervalNS
    let beforeEndNS = monotonicNS()
    if endTargetNS > beforeEndNS {
        Thread.sleep(forTimeInterval: Double(endTargetNS - beforeEndNS) / 1_000_000_000)
    }
    let flushStartNS = monotonicNS()
    try checked(VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid), "drain encoder")
    let finishedNS = monotonicNS()
    let hardwareValue = property(session, kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder)
    guard let hardware = hardwareValue as? NSNumber, hardware.boolValue else {
        throw ProbeFailure(description: "required hardware property did not read back true after encoding: \(String(describing: hardwareValue))")
    }
    let elapsedSeconds = Double(finishedNS - measuredStartNS) / 1_000_000_000
    let cpuElapsed = cpuSeconds() - measuredCPUStart
    metrics.lock.lock(); defer { metrics.lock.unlock() }
    let times = metrics.outputTimesNS.sorted()
    var outputGapMS: [Double] = []
    if times.count >= 2 {
        for i in 1..<times.count { outputGapMS.append(elapsedMS(times[i - 1], times[i])) }
    }
    var output: [String: Any] = [
        "width": width, "height": height, "codec": "H264", "input_format": "NV12 video range",
        "required_hardware": true, "using_hardware": hardware.boolValue,
        "low_latency_rate_control_requested": lowLatency,
        "frame_reordering_readback": (property(session, kVTCompressionPropertyKey_AllowFrameReordering) as? NSNumber)?.boolValue ?? true,
        "fps_target": fps, "bitrate_target_bps": bitrate,
        "warmup_seconds": warmup, "measurement_seconds_requested": seconds,
        "measurement_seconds_actual": elapsedSeconds, "submitted_measured": attempted,
        "encoded_measured": metrics.latencyMS.count, "dropped_or_failed_measured": metrics.dropped,
        "callbacks_measured": metrics.callbacks, "pending_at_end": metrics.pending,
        "pending_peak_including_warmup": metrics.pendingPeak, "keyframes_measured": metrics.keyframes,
        "encoded_fps": Double(metrics.latencyMS.count) / elapsedSeconds,
        "encoded_bitrate_bps": Double(metrics.outputBytes) * 8 / elapsedSeconds,
        "submit_to_output_callback_ms": summary(metrics.latencyMS),
        "synthetic_fill_and_pool_ms": summary(prepMS), "encode_api_call_ms": summary(submitMS),
        "input_schedule_lateness_ms": summary(scheduleLateMS), "output_gap_ms": summary(outputGapMS),
        "final_drain_ms": elapsedMS(flushStartNS, finishedNS),
        "process_cpu_percent_of_one_core_including_synthetic_fill": 100 * cpuElapsed / elapsedSeconds,
        "submission_errors": submitErrors.mapKeys(), "callback_errors": metrics.callbackErrors.mapKeys(),
        "scope": "Synthetic NV12 only; excludes emulator capture, color conversion, audio, transport, decoding and display."
    ]
    if let encoderID = property(session, kVTCompressionPropertyKey_EncoderID) as? String {
        output["encoder_id"] = encoderID
    }
    return output
}

extension Dictionary where Key == Int32, Value == Int {
    func mapKeys() -> [String: Int] { Dictionary<String, Int>(uniqueKeysWithValues: map { (String($0.key), $0.value) }) }
}

var seconds = 6.0, warmup = 1.0, bitrate = 4_000_000
var fpsValues = [30, 60]
var lowLatency = false
var args = Array(CommandLine.arguments.dropFirst())
do {
    while !args.isEmpty {
        let key = args.removeFirst()
        guard !args.isEmpty else { throw ProbeFailure(description: "missing value for \(key)") }
        let value = args.removeFirst()
        switch key {
        case "--seconds": guard let n = Double(value), n >= 1, n <= 60 else { throw ProbeFailure(description: "seconds must be 1...60") }; seconds = n
        case "--warmup": guard let n = Double(value), n >= 0, n <= 10 else { throw ProbeFailure(description: "warmup must be 0...10") }; warmup = n
        case "--bitrate": guard let n = Int(value), n >= 100_000, n <= 100_000_000 else { throw ProbeFailure(description: "invalid bitrate") }; bitrate = n
        case "--low-latency": guard value == "0" || value == "1" else { throw ProbeFailure(description: "low-latency must be 0 or 1") }; lowLatency = value == "1"
        case "--fps":
            let parsed = value.split(separator: ",").compactMap { Int($0) }
            guard !parsed.isEmpty, parsed.count <= 4, parsed.allSatisfy({ (1...120).contains($0) }) else { throw ProbeFailure(description: "invalid fps") }; fpsValues = parsed
        default: throw ProbeFailure(description: "unknown option \(key)")
        }
    }
    var reports: [[String: Any]] = []
    for fps in fpsValues { reports.append(try runProbe(fps: fps, seconds: seconds, warmup: warmup, bitrate: bitrate, lowLatency: lowLatency)) }
    let data = try JSONSerialization.data(withJSONObject: ["probe": "videotoolbox-synthetic-v1", "results": reports], options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(data); print("")
} catch {
    let data = try! JSONSerialization.data(withJSONObject: ["probe": "videotoolbox-synthetic-v1", "error": String(describing: error)], options: [.sortedKeys])
    FileHandle.standardOutput.write(data); print("")
    exit(1)
}
