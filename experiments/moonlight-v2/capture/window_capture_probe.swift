// Window-only ScreenCaptureKit -> IOSurface-backed CVPixelBuffer -> required
// Apple hardware H.264 timing probe. It never saves pixels or encoded video,
// changes displays, starts an emulator, or modifies a running service.
import Foundation
import AppKit
import ScreenCaptureKit
import CoreGraphics
import CoreMedia
import CoreVideo
import VideoToolbox
import IOSurface
import Darwin

struct ProbeFailure: Error { let message: String }
func nowNS() -> UInt64 { DispatchTime.now().uptimeNanoseconds }
func milliseconds(_ first: UInt64, _ second: UInt64) -> Double {
    Double(second >= first ? second - first : 0) / 1_000_000
}
func distribution(_ input: [Double]) -> [String: Any] {
    let values = input.sorted()
    guard !values.isEmpty else { return ["count": 0] }
    func quantile(_ q: Double) -> Double {
        let position = Double(values.count - 1) * q
        let lower = Int(position), upper = min(lower + 1, values.count - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - Double(lower))
    }
    return ["count": values.count, "p50": quantile(0.5), "p95": quantile(0.95),
            "p99": quantile(0.99), "max": values.last!]
}
func writeJSON(_ object: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
    FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
}
func allowedOwner(_ application: SCRunningApplication?) -> Bool {
    guard let application else { return false }
    let names = ["qemu-system-aarch64", "qemu-system-x86_64", "emulator", "scrcpy"]
    if names.contains(application.applicationName) { return true }
    // sys/proc_info.h defines PROC_PIDPATHINFO_MAXSIZE as 4 * MAXPATHLEN;
    // the compound macro is not imported into Swift.
    var buffer = [CChar](repeating: 0, count: 4096)
    let length = proc_pidpath(application.processID, &buffer, UInt32(buffer.count))
    guard length > 0 else { return false }
    return names.contains(URL(fileURLWithPath: String(cString: buffer)).lastPathComponent)
}

struct Options {
    var list = false, windowID: UInt32?, ownerPID: Int32?
    var duration = 10.0, warmup = 2.0, fps = 60, width = 540, bitrate = 4_000_000
    var encode = false, fingerprint = false, crop: CGRect?
    init() throws {
        let args = Array(CommandLine.arguments.dropFirst())
        var index = 0
        func value() throws -> String {
            index += 1
            guard index < args.count else { throw ProbeFailure(message: "missing option value") }
            return args[index]
        }
        while index < args.count {
            switch args[index] {
            case "--list": list = true
            case "--window-id": windowID = UInt32(try value())
            case "--owner-pid": ownerPID = Int32(try value())
            case "--duration": duration = Double(try value()) ?? -1
            case "--warmup": warmup = Double(try value()) ?? -1
            case "--fps": fps = Int(try value()) ?? -1
            case "--width": width = Int(try value()) ?? -1
            case "--bitrate": bitrate = Int(try value()) ?? -1
            case "--encode": encode = true
            case "--fingerprint": fingerprint = true
            case "--crop":
                let parts = try value().split(separator: ",", omittingEmptySubsequences: false)
                let values = parts.compactMap { Double($0) }
                guard parts.count == 4, values.count == 4, values.allSatisfy({ $0.isFinite }),
                      values[0] >= 0, values[1] >= 0, values[2] > 0, values[3] > 0 else {
                    throw ProbeFailure(message: "crop must be x,y,width,height in window points")
                }
                crop = CGRect(x: values[0], y: values[1], width: values[2], height: values[3])
            default: throw ProbeFailure(message: "unknown option: \(args[index])")
            }
            index += 1
        }
        guard 3...30 ~= duration, 0...5 ~= warmup, [30,60,120].contains(fps),
              160...1440 ~= width, width % 2 == 0, 500_000...40_000_000 ~= bitrate else {
            throw ProbeFailure(message: "bounded options: duration 3-30, warmup 0-5, FPS 30/60/120, even width 160-1440")
        }
        if !list && (windowID == nil || ownerPID == nil) {
            throw ProbeFailure(message: "capture requires explicit --window-id AND --owner-pid")
        }
    }
}

final class EncoderTicket {
    let callbackNS: UInt64, submitNS: UInt64, measured: Bool
    // Retain captured IOSurface until hardware compression completes.
    let pixel: CVPixelBuffer
    init(_ pixel: CVPixelBuffer, _ callbackNS: UInt64, _ submitNS: UInt64, _ measured: Bool) {
        self.pixel = pixel; self.callbackNS = callbackNS; self.submitNS = submitNS; self.measured = measured
    }
}
let encodeCallback: VTCompressionOutputCallback = { context, source, status, flags, sample in
    guard let context, let source else { return }
    let probe = Unmanaged<CaptureProbe>.fromOpaque(context).takeUnretainedValue()
    let ticket = Unmanaged<EncoderTicket>.fromOpaque(source).takeRetainedValue()
    probe.encoded(ticket, status, flags, sample)
}

final class CaptureProbe: NSObject, SCStreamOutput, SCStreamDelegate {
    let options: Options, outputWidth: Int, outputHeight: Int
    let lock = NSLock(), slots = DispatchSemaphore(value: 3)
    var startNS = UInt64.max, observationStartUnixMS: Int64?
    var session: VTCompressionSession?, usingHardware = false, encoderID = "unavailable"
    var callbacks = 0, complete = 0, measuredComplete = 0, iosurfaceFrames = 0
    var statusCounts: [String: Int] = [:], invalid = 0, encodeBackpressureDrops = 0
    var encodedFrames = 0, failedFrames = 0, encodedBytes = 0, pending = 0, pendingPeak = 0
    var previousCallback: UInt64?, previousPTS: Double?, previousHash: UInt64?
    var repeatedSampledImages = 0, changedSampledImages = 0
    var callbackGaps: [Double] = [], ptsGaps: [Double] = [], encodeMS: [Double] = []
    var capturedToEncodedMS: [Double] = [], attachmentAgeMS: [Double] = [], fingerprintMS: [Double] = []
    var geometry: [String: Any] = [:], stoppedError: String?

    init(options: Options, width: Int, height: Int) throws {
        self.options = options; outputWidth = width; outputHeight = height
        super.init()
        if options.encode {
            let spec = [kVTVideoEncoderSpecification_RequireHardwareAcceleratedVideoEncoder: true] as CFDictionary
            let create = VTCompressionSessionCreate(allocator: kCFAllocatorDefault,
                width: Int32(width), height: Int32(height), codecType: kCMVideoCodecType_H264,
                encoderSpecification: spec, imageBufferAttributes: nil, compressedDataAllocator: nil,
                outputCallback: encodeCallback, refcon: Unmanaged.passUnretained(self).toOpaque(),
                compressionSessionOut: &session)
            guard create == noErr, let session else { throw ProbeFailure(message: "VT create: \(create)") }
            let props: [(CFString, CFTypeRef)] = [
                (kVTCompressionPropertyKey_RealTime, kCFBooleanTrue),
                (kVTCompressionPropertyKey_AllowFrameReordering, kCFBooleanFalse),
                (kVTCompressionPropertyKey_ExpectedFrameRate, NSNumber(value: options.fps)),
                (kVTCompressionPropertyKey_AverageBitRate, NSNumber(value: options.bitrate)),
                (kVTCompressionPropertyKey_MaxKeyFrameInterval, NSNumber(value: options.fps * 2)),
                (kVTCompressionPropertyKey_MaxKeyFrameIntervalDuration, NSNumber(value: 2)),
                (kVTCompressionPropertyKey_ProfileLevel, kVTProfileLevel_H264_Baseline_AutoLevel)]
            for (key, value) in props {
                let status = VTSessionSetProperty(session, key: key, value: value)
                guard status == noErr else { throw ProbeFailure(message: "VT property \(key): \(status)") }
            }
            let prepare = VTCompressionSessionPrepareToEncodeFrames(session)
            guard prepare == noErr else { throw ProbeFailure(message: "VT prepare: \(prepare)") }
            var value: Unmanaged<CFTypeRef>?
            guard VTSessionCopyProperty(session, key: kVTCompressionPropertyKey_UsingHardwareAcceleratedVideoEncoder,
                    allocator: kCFAllocatorDefault, valueOut: &value) == noErr,
                  (value?.takeRetainedValue() as? NSNumber)?.boolValue == true else {
                throw ProbeFailure(message: "required hardware encoder was not selected")
            }
            usingHardware = true
            value = nil
            if VTSessionCopyProperty(session, key: kVTCompressionPropertyKey_EncoderID,
                    allocator: kCFAllocatorDefault, valueOut: &value) == noErr {
                encoderID = value?.takeRetainedValue() as? String ?? "unavailable"
            }
        }
    }
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        lock.lock(); stoppedError = String(describing: type(of: error)); lock.unlock()
    }
    func beginClock() {
        lock.lock(); startNS = nowNS()
        observationStartUnixMS = Int64(Date().timeIntervalSince1970 * 1000)
        lock.unlock()
    }
    func sampledHash(_ pixel: CVPixelBuffer) -> UInt64? {
        // Optional 64x128 grid. An equality means sampled pixels matched, NOT
        // proof that every pixel matched; report this distinction explicitly.
        guard CVPixelBufferLockBaseAddress(pixel, .readOnly) == kCVReturnSuccess else { return nil }
        defer { CVPixelBufferUnlockBaseAddress(pixel, .readOnly) }
        guard let raw = CVPixelBufferGetBaseAddress(pixel) else { return nil }
        let bytes = raw.assumingMemoryBound(to: UInt8.self)
        let width = CVPixelBufferGetWidth(pixel), height = CVPixelBufferGetHeight(pixel)
        let row = CVPixelBufferGetBytesPerRow(pixel)
        var hash: UInt64 = 14695981039346656037
        for gy in 0..<128 {
            let y = min(height - 1, gy * height / 128)
            for gx in 0..<64 {
                let x = min(width - 1, gx * width / 64), offset = y * row + x * 4
                for c in 0..<3 { hash = (hash ^ UInt64(bytes[offset + c])) &* 1099511628211 }
            }
        }
        return hash
    }
    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .screen else { return }
        let arrived = nowNS()
        lock.lock(); let origin = startNS; lock.unlock()
        let elapsed = arrived >= origin ? Double(arrived - origin) / 1e9 : -1
        let measured = elapsed >= options.warmup && elapsed < options.warmup + options.duration
        let attachments = CMSampleBufferGetSampleAttachmentsArray(sample, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]]
        let info = attachments?.first ?? [:]
        let status = (info[.status] as? NSNumber)?.intValue ?? -1
        lock.lock(); callbacks += 1; statusCounts[String(status), default: 0] += 1; lock.unlock()
        guard CMSampleBufferIsValid(sample), status == SCFrameStatus.complete.rawValue,
              let pixel = CMSampleBufferGetImageBuffer(sample) else { return }
        let w = CVPixelBufferGetWidth(pixel), h = CVPixelBufferGetHeight(pixel)
        guard w == outputWidth && h == outputHeight else { lock.lock(); invalid += 1; lock.unlock(); return }
        let pts = CMSampleBufferGetPresentationTimeStamp(sample)
        let ptsSeconds = CMTimeGetSeconds(pts)
        lock.lock()
        complete += 1
        if measured {
            measuredComplete += 1
            if CVPixelBufferGetIOSurface(pixel) != nil { iosurfaceFrames += 1 }
            if let previousCallback { callbackGaps.append(milliseconds(previousCallback, arrived)) }
            self.previousCallback = arrived
            if let previousPTS, ptsSeconds.isFinite { ptsGaps.append((ptsSeconds - previousPTS) * 1000) }
            if ptsSeconds.isFinite { previousPTS = ptsSeconds }
            if let displayTime = info[.displayTime] as? NSNumber {
                // displayTime is mach_absolute_time ticks. Convert to host ns;
                // DispatchTime.uptimeNanoseconds uses the same uptime clock.
                var scale = mach_timebase_info_data_t(); mach_timebase_info(&scale)
                let sourceNS = UInt64(Double(displayTime.uint64Value) * Double(scale.numer) / Double(scale.denom))
                if arrived >= sourceNS, arrived - sourceNS < 5_000_000_000 {
                    attachmentAgeMS.append(milliseconds(sourceNS, arrived))
                }
            }
            if geometry.isEmpty {
                geometry = ["pixel_size": [w,h], "bytes_per_row": CVPixelBufferGetBytesPerRow(pixel)]
                if let rect = info[.contentRect] as? CGRect {
                    geometry["content_rect"] = [rect.origin.x, rect.origin.y, rect.width, rect.height]
                }
                if let scale = info[.scaleFactor] as? NSNumber { geometry["scale_factor"] = scale }
                if let scale = info[.contentScale] as? NSNumber { geometry["content_scale"] = scale }
            }
        }
        lock.unlock()
        if measured && options.fingerprint {
            let hashStart = nowNS(), hash = sampledHash(pixel), hashEnd = nowNS()
            lock.lock()
            fingerprintMS.append(milliseconds(hashStart, hashEnd))
            if let hash {
                if let previousHash {
                    if hash == previousHash { repeatedSampledImages += 1 } else { changedSampledImages += 1 }
                }
                previousHash = hash
            }
            lock.unlock()
        }
        guard let session else { return }
        guard slots.wait(timeout: .now()) == .success else {
            if measured { lock.lock(); encodeBackpressureDrops += 1; lock.unlock() }
            return
        }
        let submit = nowNS(), ticket = Unmanaged.passRetained(EncoderTicket(pixel, arrived, submit, measured))
        lock.lock(); pending += 1; pendingPeak = max(pendingPeak, pending); lock.unlock()
        let statusCode = VTCompressionSessionEncodeFrame(session, imageBuffer: pixel,
            presentationTimeStamp: pts, duration: CMTime(value: 1, timescale: Int32(options.fps)),
            frameProperties: nil, sourceFrameRefcon: ticket.toOpaque(), infoFlagsOut: nil)
        if statusCode != noErr { encoded(ticket.takeRetainedValue(), statusCode, [], nil) }
    }
    func encoded(_ ticket: EncoderTicket, _ status: OSStatus, _ flags: VTEncodeInfoFlags, _ sample: CMSampleBuffer?) {
        let end = nowNS()
        lock.lock(); pending -= 1
        if ticket.measured {
            if status == noErr, !flags.contains(.frameDropped), let sample {
                encodedFrames += 1
                encodedBytes += CMSampleBufferGetTotalSampleSize(sample)
                encodeMS.append(milliseconds(ticket.submitNS, end))
                capturedToEncodedMS.append(milliseconds(ticket.callbackNS, end))
            } else { failedFrames += 1 }
        }
        lock.unlock(); slots.signal()
    }
    func finish() -> [String: Any] {
        if let session { VTCompressionSessionCompleteFrames(session, untilPresentationTimeStamp: .invalid) }
        lock.lock(); defer { lock.unlock() }
        return ["probe": "window-sck-iosurface-vt-v1", "requested_fps_cap": options.fps,
            "warmup_seconds": options.warmup, "measurement_seconds": options.duration,
            "observation_start_unix_ms": observationStartUnixMS as Any? ?? NSNull(),
            "callbacks_including_warmup": callbacks, "complete_including_warmup": complete,
            "frame_status_raw_counts_including_warmup": statusCounts,
            "complete_frames": measuredComplete, "complete_fps": Double(measuredComplete) / options.duration,
            "encoded_frames": encodedFrames, "encoded_fps": Double(encodedFrames) / options.duration,
            "encoded_megabits_per_second": Double(encodedBytes) * 8 / options.duration / 1e6,
            "iosurface_backed_complete_frames": iosurfaceFrames, "geometry": geometry,
            "hardware_required": options.encode, "hardware_selected": usingHardware, "encoder_id": encoderID,
            "sck_queue_depth": 3, "vt_pending_peak": pendingPeak, "invalid_geometry_frames": invalid,
            "vt_backpressure_dropped": encodeBackpressureDrops, "vt_failed_or_dropped": failedFrames,
            "callback_gap_ms": distribution(callbackGaps), "pts_gap_ms": distribution(ptsGaps),
            "repeated_pts_transitions": ptsGaps.filter { $0 == 0 }.count,
            "nonmonotonic_pts_transitions": ptsGaps.filter { $0 < 0 }.count,
            "sck_display_time_to_callback_ms": distribution(attachmentAgeMS),
            "vt_submit_to_callback_ms": distribution(encodeMS),
            "sck_callback_to_encoded_ms": distribution(capturedToEncodedMS),
            "sampled_image_fingerprint_enabled": options.fingerprint,
            "sampled_image_changed_transitions": changedSampledImages,
            "sampled_image_repeated_transitions": repeatedSampledImages,
            "sampled_image_fingerprint_ms": distribution(fingerprintMS),
            "stopped_error_type": stoppedError as Any? ?? NSNull(),
            "pixel_or_encoded_video_persistence": false,
            "scope": "selected emulator/scrcpy window only; no desktop capture. Complete or unique PTS is not unique content FPS. Optional CPU grid hash can miss unsampled changes and adds measured readback cost. VT output is discarded; no phone, network, audio or input is measured. CVPixelBuffer is passed directly to VT without an application pixel copy; internal compositor/encoder copies are not ruled out."]
    }
}

@main struct Main {
    @MainActor
    static func main() async {
        do {
            let options = try Options()
            // Initialize the GUI connection for window-only SCContentFilter.
            // No window is created, activated, moved or resized.
            _ = NSApplication.shared
            guard CGPreflightScreenCaptureAccess() else {
                writeJSON(["status": "screen_recording_preflight_false", "permission_requested": false,
                           "scope": "no stream was created; preflight false may reflect sandbox restrictions or missing TCC grant. Grant was not requested by this probe"])
                Darwin.exit(3)
            }
            let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: false)
            let allowed = content.windows.filter {
                allowedOwner($0.owningApplication) && (options.ownerPID == nil || $0.owningApplication?.processID == options.ownerPID)
            }
            if options.list {
                writeJSON(["status": "ok", "windows": allowed.map { window -> [String: Any] in
                    ["window_id": window.windowID, "owner_pid": window.owningApplication?.processID ?? -1,
                     "owner": window.owningApplication?.applicationName ?? "", "title": window.title ?? "",
                     "frame_points": [window.frame.origin.x, window.frame.origin.y, window.frame.width, window.frame.height]]
                }]); return
            }
            guard let window = allowed.first(where: { $0.windowID == options.windowID && $0.owningApplication?.processID == options.ownerPID }) else {
                throw ProbeFailure(message: "selected ID/PID is not an allowed emulator/scrcpy window")
            }
            let crop = options.crop ?? CGRect(origin: .zero, size: window.frame.size)
            guard crop.maxX <= window.frame.width, crop.maxY <= window.frame.height else {
                throw ProbeFailure(message: "crop exceeds the selected window; desktop capture is refused")
            }
            let outputHeight = max(2, Int((Double(options.width) * crop.height / crop.width / 2).rounded()) * 2)
            guard 2...4096 ~= outputHeight else { throw ProbeFailure(message: "output height outside bounded probe geometry") }
            let configuration = SCStreamConfiguration()
            configuration.width = options.width; configuration.height = outputHeight
            configuration.minimumFrameInterval = CMTime(value: 1, timescale: Int32(options.fps))
            configuration.queueDepth = 3; configuration.pixelFormat = kCVPixelFormatType_32BGRA
            configuration.showsCursor = false; configuration.capturesAudio = false
            configuration.sourceRect = crop
            if #available(macOS 14.0, *) { configuration.ignoreShadowsSingleWindow = true }
            let probe = try CaptureProbe(options: options, width: options.width, height: outputHeight)
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let stream = SCStream(filter: filter, configuration: configuration, delegate: probe)
            try stream.addStreamOutput(probe, type: .screen, sampleHandlerQueue: DispatchQueue(label: "huoguo.capture.probe"))
            try await stream.startCapture()
            probe.beginClock()
            try await Task.sleep(nanoseconds: UInt64((options.warmup + options.duration) * 1e9))
            try await stream.stopCapture()
            var report = probe.finish()
            report["status"] = "ok"; report["window_id"] = window.windowID
            report["owner_pid"] = window.owningApplication?.processID ?? -1
            report["owner"] = window.owningApplication?.applicationName ?? ""
            report["window_frame_points"] = [window.frame.width, window.frame.height]
            report["crop_points"] = [crop.origin.x, crop.origin.y, crop.width, crop.height]
            report["crop_is_full_window"] = options.crop == nil
            writeJSON(report)
        } catch {
            writeJSON(["status": "error", "error": (error as? ProbeFailure)?.message ?? String(describing: type(of: error))])
            Darwin.exit(1)
        }
    }
}
