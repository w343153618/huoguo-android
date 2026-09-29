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

import Foundation
import CoreVideo
import CoreMedia
import VideoToolbox
import Accelerate
import Darwin

struct EncoderFailure: Error, CustomStringConvertible { let description: String }
func check(_ status: OSStatus, _ action: String) throws {
    if status != noErr { throw EncoderFailure(description: "\(action): OSStatus \(status)") }
}
func nowNS() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
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

final class FrameContext {
    let pts: UInt64, rawReadyNS: UInt64, submitNS: UInt64, pixel: CVPixelBuffer
    init(_ pts: UInt64, _ rawReadyNS: UInt64, _ submitNS: UInt64, _ pixel: CVPixelBuffer) {
        self.pts = pts; self.rawReadyNS = rawReadyNS; self.submitNS = submitNS; self.pixel = pixel
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
    let startedNS = nowNS(), cpuStart = cpuSeconds()
    let lock = NSLock(), outputLock = NSLock(), slots = DispatchSemaphore(value: 3)
    var session: VTCompressionSession?, pool: CVPixelBufferPool?
    var submitted = 0, encoded = 0, dropped = 0, keyframes = 0, bytesOutput = 0, pending = 0, pendingPeak = 0
    var conversionMS: [Double] = [], encodeMS: [Double] = [], fullMS: [Double] = [], writeMS: [Double] = []
    var captureAgeMS: [Double] = [], outputTimesNS: [UInt64] = []
    var errorMessage: String?, previousConfig = Data(), previousPTS: UInt64?
    var frameDelaySetStatus: [String: Int] = [:]
    var rateLimitStatus: Int?
    var forceKeyframe = false

    init(width: Int, height: Int, fps: Int, bitrate: Int, bitrateMode: String = "VBR", emitCodec: Bool = true) throws {
        self.width = width; self.height = height; self.fps = fps; self.bitrate = bitrate
        self.bitrateMode = bitrateMode
        let pixelAttributes: [CFString: Any] = [
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey: width, kCVPixelBufferHeightKey: height,
            kCVPixelBufferIOSurfacePropertiesKey: [:]
        ]
        let specification: [CFString: Any] = [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: kCFBooleanTrue!]
        try check(VTCompressionSessionCreate(allocator: kCFAllocatorDefault, width: Int32(width), height: Int32(height),
            codecType: kCMVideoCodecType_H264, encoderSpecification: specification as CFDictionary,
            imageBufferAttributes: pixelAttributes as CFDictionary, compressedDataAllocator: nil,
            outputCallback: outputCallback, refcon: Unmanaged.passUnretained(self).toOpaque(),
            compressionSessionOut: &session), "create required hardware H264")
        guard let session = session else { throw EncoderFailure(description: "missing VT session") }
        let properties: [(CFString, CFTypeRef)] = [
            (kVTCompressionPropertyKey_RealTime, kCFBooleanTrue),
            (kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse),
            (kVTCompressionPropertyKey_ProfileLevel, kVTProfileLevel_H264_Baseline_AutoLevel),
            (kVTCompressionPropertyKey_ExpectedFrameRate, NSNumber(value: fps)),
            (kVTCompressionPropertyKey_AverageBitRate, NSNumber(value: bitrate)),
            (kVTCompressionPropertyKey_MaxKeyFrameInterval, NSNumber(value: fps * 2)),
            (kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, NSNumber(value: 2.0)),
            (kVTCompressionPropertyKey_ColorPrimaries, kCVImageBufferColorPrimaries_ITU_R_709_2),
            (kVTCompressionPropertyKey_TransferFunction, kCVImageBufferTransferFunction_ITU_R_709_2),
            (kVTCompressionPropertyKey_YCbCrMatrix, kCVImageBufferYCbCrMatrix_ITU_R_709_2)
        ]
        for (key, value) in properties { try check(VTSessionSetProperty(session, key: key, value: value), "set \(key)") }
        if bitrateMode == "CBR" {
            try check(VTSessionSetProperty(session, key: "ConstantBitRate" as CFString,
                                          value: NSNumber(value: bitrate)), "set required CBR target")
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
        try check(VTCompressionSessionPrepareToEncodeFrames(session), "prepare hardware encoder")
        guard let hardware = try sessionProperty(session, kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder) as? NSNumber,
              hardware.boolValue else { throw EncoderFailure(description: "hardware encoder required at startup") }
        logJSON(["event": "ready", "using_hardware": true, "bitrate_mode": bitrateMode,
                 "width": width, "height": height,
                 "vbr_rate_limit_status": rateLimitStatus as Any? ?? NSNull(),
                 "encoder_id": (try? sessionProperty(session, kVTCompressionPropertyKey_EncoderID)) as? String ?? "unavailable"])
        let poolAttributes = [kCVPixelBufferPoolMinimumBufferCountKey: 3] as CFDictionary
        try check(CVPixelBufferPoolCreate(kCFAllocatorDefault, poolAttributes, pixelAttributes as CFDictionary, &pool), "create IOSurface pixel pool")
        var header = emitCodec ? Data("h264".utf8) : Data()
        appendBE(UInt32(0x80000000), to: &header); appendBE(UInt32(width), to: &header); appendBE(UInt32(height), to: &header)
        try writeAll(header)
    }
    deinit { if let session = session { VTCompressionSessionInvalidate(session) } }

    private func setRateLimit(_ session: VTCompressionSession, _ target: Int) -> OSStatus {
        // AverageBitRate is only a long-term target. Limit one-second VBR bursts
        // to 1.5x so a short-video scene cut cannot flood a shallow jitter buffer.
        let bytesPerSecond = NSNumber(value: Int64(target) * 3 / 16)
        let oneSecond = NSNumber(value: 1.0)
        return VTSessionSetProperty(session, key: kVTCompressionPropertyKey_DataRateLimits,
                                    value: [bytesPerSecond, oneSecond] as CFArray)
    }

    func updateBitrate(_ value: Int, sequence: UInt64) -> Bool {
        var status: OSStatus = kVTInvalidSessionErr
        if let session = session, (500_000...100_000_000).contains(value) {
            let key = bitrateMode == "CBR" ? "ConstantBitRate" as CFString : kVTCompressionPropertyKey_AverageBitRate
            status = VTSessionSetProperty(session, key: key, value: NSNumber(value: value))
            if status == noErr && bitrateMode != "CBR" { rateLimitStatus = Int(setRateLimit(session, value)) }
        }
        logJSON(["event": "bitrate", "sequence": sequence, "accepted_bitrate": status == noErr ? value : 0,
                 "status": Int(status), "vbr_rate_limit_status": rateLimitStatus as Any? ?? NSNull()])
        return status == noErr
    }

    func submit(_ rgba: Data, pts: UInt64, rawReadyNS: UInt64) throws {
        if let prior = previousPTS, pts <= prior { throw EncoderFailure(description: "input PTS must increase") }
        if pts >= (UInt64(1) << 61) { throw EncoderFailure(description: "PTS uses protocol flag bits") }
        lock.lock(); let failure = errorMessage; lock.unlock()
        if let failure = failure { throw EncoderFailure(description: failure) }
        guard slots.wait(timeout: .now() + 2) == .success else { throw EncoderFailure(description: "encoder backlog exceeded 3 frames for 2 seconds") }
        var slotTransferred = false
        defer { if !slotTransferred { slots.signal() } }
        guard let session = session, let pool = pool else { throw EncoderFailure(description: "encoder unavailable") }
        let conversionStart = nowNS()
        var buffer: CVPixelBuffer?
        try check(CVPixelBufferPoolCreatePixelBuffer(kCFAllocatorDefault, pool, &buffer), "allocate pooled BGRA")
        guard let pixel = buffer else { throw EncoderFailure(description: "missing pixel buffer") }
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
        let ticket = Unmanaged.passRetained(FrameContext(pts, rawReadyNS, submitNS, pixel))
        lock.lock()
        submitted += 1; pending += 1; pendingPeak = max(pendingPeak, pending)
        if conversionMS.count < 3600 { conversionMS.append(ms(conversionStart, submitNS)) }
        let unixUS = UInt64(Date().timeIntervalSince1970 * 1e6)
        if pts > 1_000_000_000_000 && unixUS >= pts && unixUS - pts < 5_000_000 {
            if captureAgeMS.count < 3600 { captureAgeMS.append(Double(unixUS - pts) / 1000) }
        }
        lock.unlock()
        slotTransferred = true
        let frameProperties = forceKeyframe ? [kVTEncodeFrameOptionKey_ForceKeyFrame: kCFBooleanTrue!] as CFDictionary : nil
        forceKeyframe = false
        let status = VTCompressionSessionEncodeFrame(session, imageBuffer: pixel,
            presentationTimeStamp: CMTime(value: Int64(pts), timescale: 1_000_000),
            duration: CMTime(value: 1, timescale: Int32(fps)), frameProperties: frameProperties,
            sourceFrameRefcon: ticket.toOpaque(), infoFlagsOut: nil)
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
            outputLock.lock(); defer { outputLock.unlock() }
            let writeStart = nowNS()
            if configuration != previousConfig {
                try packet(pts: UInt64(1) << 62, payload: configuration); previousConfig = configuration
            }
            try packet(pts: frame.pts | (keyframe ? UInt64(1) << 61 : 0), payload: annexB)
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
        guard let hardware = try sessionProperty(session, kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder) as? NSNumber,
              hardware.boolValue else { throw EncoderFailure(description: "actual hardware readback is not true") }
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
            "required_hardware": true, "using_hardware": hardware.boolValue, "encoder_id": encoderID,
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
var service = false, bitrateMode = "VBR"
var arguments = Array(CommandLine.arguments.dropFirst()), encoder: HardwareEncoder?
do {
    while !arguments.isEmpty {
        let key = arguments.removeFirst(); guard !arguments.isEmpty else { throw EncoderFailure(description: "missing value for \(key)") }
        let value = arguments.removeFirst()
        switch key {
        case "--service": guard value == "true" else { throw EncoderFailure(description: "service expects true") }; service = true
        case "--mode": guard value == "CBR" || value == "VBR" else { throw EncoderFailure(description: "mode expects CBR or VBR") }; bitrateMode = value
        case "--fps": guard let n = Int(value), (1...120).contains(n) else { throw EncoderFailure(description: "fps must be 1...120") }; fps = n
        case "--bitrate": guard let n = Int(value), (100_000...100_000_000).contains(n) else { throw EncoderFailure(description: "invalid bitrate") }; bitrate = n
        case "--max-frames": guard let n = Int(value), (1...3600).contains(n) else { throw EncoderFailure(description: "max-frames must be 1...3600") }; maxFrames = n
        case "--max-seconds": guard let n = Double(value), n >= 1, n <= 120 else { throw EncoderFailure(description: "max-seconds must be 1...120") }; maxSeconds = n
        case "--idle-seconds": guard let n = Double(value), n >= 1, n <= 30 else { throw EncoderFailure(description: "idle-seconds must be 1...30") }; idleMS = Int32(n * 1000)
        default: throw EncoderFailure(description: "unknown option \(key)")
        }
    }
    let started = nowNS(), inputDeadline = service ? UInt64.max : started + UInt64(maxSeconds * 1e9)
    var frames = 0, reason = "EOF", emittedCodec = false
    while service || frames < maxFrames {
        if !service && Double(nowNS() - started) / 1e9 >= maxSeconds { reason = "time_limit"; break }
        guard let header = try readExact(20, idleMS: idleMS, deadlineNS: inputDeadline, cleanEOF: true) else { break }
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
        guard width >= 2, height >= 2, width <= 8192, height <= 8192, width % 2 == 0, height % 2 == 0,
              length == width * height * 4, length <= 64 * 1024 * 1024 else { throw EncoderFailure(description: "invalid complete RGBA frame dimensions/length") }
        guard let rgba = try readExact(length, idleMS: idleMS, deadlineNS: inputDeadline) else { throw EncoderFailure(description: "missing RGBA") }
        let ready = nowNS()
        if service, let current = encoder, width != current.width || height != current.height {
            try current.finish(reason: "resize"); encoder = nil
        }
        if encoder == nil {
            encoder = try HardwareEncoder(width: width, height: height, fps: fps, bitrate: bitrate,
                                          bitrateMode: bitrateMode, emitCodec: !emittedCodec)
            emittedCodec = true
        }
        guard let current = encoder, width == current.width, height == current.height else { throw EncoderFailure(description: "resolution changes require a new prototype process") }
        try current.submit(rgba, pts: pts, rawReadyNS: ready); frames += 1
    }
    if !service && frames == maxFrames { reason = "frame_limit" }
    if let encoder = encoder { try encoder.finish(reason: reason) }
    else { logJSON(["probe": "emulator-hardware-rgba-v1", "input_frames": 0, "finish_reason": reason]) }
} catch {
    logJSON(["probe": "emulator-hardware-rgba-v1", "error": String(describing: error)])
    // In-flight frames are bounded at 3 and stdout callbacks time out. Attempt
    // cleanup without allowing a wedged native drain to hang an isolated probe.
    DispatchQueue.global().asyncAfter(deadline: .now() + 8) { _exit(1) }
    if let encoder = encoder { try? encoder.finish(reason: "error") }
    exit(1)
}
