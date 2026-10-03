// Output-only, event-driven CoreAudio guard. No guest or input-audio operation.
import Foundation
import CoreAudio

func address(_ selector: AudioObjectPropertySelector,
             _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal,
             _ element: AudioObjectPropertyElement = kAudioObjectPropertyElementMain) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: element)
}

enum OutputPolicy {
    static func physical(_ transport: UInt32) -> Bool {
        [kAudioDeviceTransportTypeBuiltIn, kAudioDeviceTransportTypePCI,
         kAudioDeviceTransportTypeUSB, kAudioDeviceTransportTypeFireWire,
         kAudioDeviceTransportTypeBluetooth, kAudioDeviceTransportTypeBluetoothLE,
         kAudioDeviceTransportTypeHDMI, kAudioDeviceTransportTypeDisplayPort,
         kAudioDeviceTransportTypeThunderbolt].contains(transport)
    }
    static func volumeNeedsZero(_ value: Float32) -> Bool { value.isFinite && value > 0 }
    static func allChannelsProtected(_ channels: Int, _ protected: Set<Int>) -> Bool {
        channels > 0 && channels <= 64 && (1...channels).allSatisfy { protected.contains($0) }
    }
    // The fallback is allowed only for an actual current physical default.
    // A pinned speaker's missing control must not reroute an unrelated default.
    static func routeFallback(isCurrent: Bool, isPhysical: Bool, confirmed: Bool,
                              sinkFound: Bool, alreadySink: Bool) -> Bool {
        isCurrent && isPhysical && !confirmed && sinkFound && !alreadySink
    }
}

struct HAL {
    static func get<T>(_ object: AudioObjectID, _ property: AudioObjectPropertyAddress,
                       initial: T) -> T? {
        var a = property, value = initial
        var size = UInt32(MemoryLayout<T>.size)
        let result = withUnsafeMutablePointer(to: &value) {
            AudioObjectGetPropertyData(object, &a, 0, nil, &size, $0)
        }
        return result == noErr && size == MemoryLayout<T>.size ? value : nil
    }
    static func writable(_ object: AudioObjectID, _ property: AudioObjectPropertyAddress) -> Bool {
        var a = property, writable = DarwinBoolean(false)
        return AudioObjectHasProperty(object, &a)
            && AudioObjectIsPropertySettable(object, &a, &writable) == noErr && writable.boolValue
    }
    static func set<T>(_ object: AudioObjectID, _ property: AudioObjectPropertyAddress, _ value: T) -> Bool {
        guard writable(object, property) else { return false }
        var a = property, v = value
        return withUnsafePointer(to: &v) {
            AudioObjectSetPropertyData(object, &a, 0, nil, UInt32(MemoryLayout<T>.size), $0)
        } == noErr
    }
    static func uid(_ device: AudioObjectID) -> String? {
        var a = address(kAudioDevicePropertyDeviceUID)
        let slot = UnsafeMutablePointer<Unmanaged<CFString>?>.allocate(capacity: 1)
        slot.initialize(to: nil)
        defer { slot.deinitialize(count: 1); slot.deallocate() }
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        guard AudioObjectGetPropertyData(device, &a, 0, nil, &size, slot) == noErr,
              let value = slot.pointee else { return nil }
        // DeviceUID Get returns a retained CFString, per the SDK contract.
        return value.takeRetainedValue() as String
    }
    static func devices() -> [AudioObjectID] {
        var a = address(kAudioHardwarePropertyDevices), size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size) == noErr,
              size > 0, size <= 4096, size % UInt32(MemoryLayout<AudioObjectID>.size) == 0 else { return [] }
        var values = [AudioObjectID](repeating: 0, count: Int(size)/MemoryLayout<AudioObjectID>.size)
        let status = values.withUnsafeMutableBytes {
            AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &a, 0, nil, &size, $0.baseAddress!)
        }
        return status == noErr ? values : []
    }
    static func channels(_ device: AudioObjectID) -> Int? {
        var a = address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeOutput), size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &a, 0, nil, &size) == noErr,
              size >= MemoryLayout<AudioBufferList>.size, size <= 16384 else { return nil }
        let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
        defer { raw.deallocate() }
        guard AudioObjectGetPropertyData(device, &a, 0, nil, &size, raw) == noErr else { return nil }
        let list = raw.assumingMemoryBound(to: AudioBufferList.self)
        let count = UnsafeMutableAudioBufferListPointer(list).reduce(0) { $0 + Int($1.mNumberChannels) }
        return count <= 64 ? count : nil
    }
    static func defaultDevice(_ selector: AudioObjectPropertySelector) -> AudioObjectID? {
        let value: UInt32? = get(AudioObjectID(kAudioObjectSystemObject), address(selector), initial: UInt32(0))
        return value.flatMap { $0 != kAudioObjectUnknown ? $0 : nil }
    }
}

func emit(_ record: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]) else { return }
    FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10]))
}

struct DeviceProtection {
    let confirmed: Bool
    let writable: Bool
    let writes: Int
}

final class OutputGuard {
    let queue = DispatchQueue(label: "local.huoguo.m5.output-guard")
    let sinkUID: String?, speakerUID: String
    var pending = false
    var listenerErrors = 0
    var lastHealthy = false
    var lastStatusSignature = ""
    var protectedDevices: [AudioObjectID: AudioObjectPropertyListenerBlock] = [:]
    var systemListeners: [(AudioObjectPropertyAddress, AudioObjectPropertyListenerBlock)] = []

    init(sinkUID: String?, speakerUID: String) { self.sinkUID = sinkUID; self.speakerUID = speakerUID }

    func schedule() {
        // Called only on this serial queue; notifications are coalesced. No timer.
        if pending { return }; pending = true
        queue.async { self.pending = false; self.reconcile() }
    }
    func start() {
        queue.sync {
            for selector in [kAudioHardwarePropertyDefaultOutputDevice,
                             kAudioHardwarePropertyDefaultSystemOutputDevice,
                             kAudioHardwarePropertyDevices] {
                var a = address(selector)
                let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.schedule() }
                if AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &a, queue, block) == noErr {
                    systemListeners.append((a, block))
                } else { listenerErrors += 1 }
            }
            reconcile()
        }
    }
    func protect(_ device: AudioObjectID) -> DeviceProtection {
        let count = HAL.channels(device)
        let elements = [0] + (count.map { $0 > 0 ? Array(1...$0) : [] } ?? [])
        var master = false, channels = Set<Int>(), writable = false, writes = 0
        for element in elements {
            var confirmed = false
            let mute = address(kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput, UInt32(element))
            if HAL.writable(device, mute) {
                writable = true
                if let value: UInt32 = HAL.get(device, mute, initial: UInt32(0)) {
                    if value == 0 && HAL.set(device, mute, UInt32(1)) { writes += 1 }
                    confirmed = (HAL.get(device, mute, initial: UInt32(0)) as UInt32?) == 1
                }
            }
            let volume = address(kAudioDevicePropertyVolumeScalar, kAudioObjectPropertyScopeOutput, UInt32(element))
            if HAL.writable(device, volume) {
                writable = true
                if let value: Float32 = HAL.get(device, volume, initial: Float32(-1)) {
                    if OutputPolicy.volumeNeedsZero(value) && HAL.set(device, volume, Float32(0)) { writes += 1 }
                    if let after: Float32 = HAL.get(device, volume, initial: Float32(-1)), after.isFinite && after == 0 {
                        confirmed = true
                    }
                }
            }
            if confirmed { if element == 0 { master = true } else { channels.insert(element) } }
        }
        return DeviceProtection(confirmed: master || OutputPolicy.allChannelsProtected(count ?? 0, channels), writable: writable, writes: writes)
    }
    func reconcile(watch: Bool = true) {
        let all = HAL.devices()
        let output = HAL.defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
        let system = HAL.defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice)
        let sink = sinkUID.flatMap { wanted in all.first { HAL.uid($0) == wanted && (HAL.channels($0) ?? 0) > 0 } }
        let speaker = all.first { HAL.uid($0) == speakerUID }
        let validatedSpeaker = speaker.flatMap { id -> AudioObjectID? in
            let transport: UInt32? = HAL.get(id, address(kAudioDevicePropertyTransportType), initial: UInt32(0))
            return transport == kAudioDeviceTransportTypeBuiltIn && (HAL.channels(id) ?? 0) > 0 ? id : nil
        }
        let current = Set([output, system].compactMap { $0 })
        var unclassified = 0, ignoredVirtual = 0
        for id in current {
            let transport: UInt32? = HAL.get(id, address(kAudioDevicePropertyTransportType), initial: UInt32(0))
            if transport == kAudioDeviceTransportTypeVirtual { ignoredVirtual += 1 }
            else if transport.map(OutputPolicy.physical) != true { unclassified += 1 }
        }
        // Default virtual/aggregate/Oray devices are never muted or selected as
        // a sink by name. Include only recognized physical outputs and the exact
        // preaudited built-in speaker UID, verified as built-in on every event.
        let targets = Set(current.filter {
            let transport: UInt32? = HAL.get($0, address(kAudioDevicePropertyTransportType), initial: UInt32(0))
            return transport.map(OutputPolicy.physical) ?? false
        }).union(validatedSpeaker.map { [$0] } ?? [])
        for (id, block) in protectedDevices where watch && !targets.contains(id) {
            var a = address(kAudioObjectPropertySelectorWildcard, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementWildcard)
            if AudioObjectRemovePropertyListenerBlock(id, &a, queue, block) != noErr { listenerErrors += 1 }
            protectedDevices.removeValue(forKey: id)
        }
        for id in targets where watch && protectedDevices[id] == nil {
            var a = address(kAudioObjectPropertySelectorWildcard, kAudioObjectPropertyScopeOutput, kAudioObjectPropertyElementWildcard)
            let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.schedule() }
            if AudioObjectAddPropertyListenerBlock(id, &a, queue, block) == noErr { protectedDevices[id] = block }
            else { listenerErrors += 1 }
        }
        var writes = 0, uncertain = 0, failedWritable = 0, routed = 0
        for id in targets {
            let result = protect(id); writes += result.writes
            if !result.confirmed { uncertain += 1 }
            if result.writable && !result.confirmed { failedWritable += 1 }
            if OutputPolicy.routeFallback(isCurrent: current.contains(id), isPhysical: true,
                                          confirmed: result.confirmed, sinkFound: sink != nil, alreadySink: id == sink), let sink {
                // Route only the affected default selector, never a pinned but
                // no-longer-default speaker, never input/default-input selectors.
                for selector in [kAudioHardwarePropertyDefaultOutputDevice, kAudioHardwarePropertyDefaultSystemOutputDevice]
                    where HAL.defaultDevice(selector) == id {
                    if HAL.set(AudioObjectID(kAudioObjectSystemObject), address(selector), sink) { routed += 1 }
                }
            }
        }
        lastHealthy = uncertain == 0 && unclassified == 0 && listenerErrors == 0 && validatedSpeaker != nil && output != nil && system != nil
        // Output wildcard notifications can be frequent. Log initial/changed
        // policy state or an actual correction, not identical no-op events.
        let signature = "\(output ?? 0)/\(system ?? 0)/\(targets.sorted())/\(uncertain)/\(unclassified)/\(ignoredVirtual)/\(listenerErrors)/\(lastHealthy)"
        if signature == lastStatusSignature && writes == 0 && routed == 0 { return }
        lastStatusSignature = signature
        emit(["schema": 1, "event": "output_reconcile", "protected_physical_devices": targets.count,
              "writes": writes, "uncertain_devices": uncertain, "sink_routes": routed,
              "failed_writable_control_devices": failedWritable,
              "unclassified_default_devices": unclassified, "ignored_virtual_default_devices": ignoredVirtual,
              "all_output_routes_quiet_verified": false,
              "default_output_id": output ?? 0, "default_system_output_id": system ?? 0,
              "built_in_speaker_found": validatedSpeaker != nil,
              "confirmed_devices": targets.count - uncertain, "desired_state_confirmed": lastHealthy,
              "listener_errors": listenerErrors, "input_changed": false, "guest_changed": false])
    }
}

func audit() {
    let output = HAL.defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
    let system = HAL.defaultDevice(kAudioHardwarePropertyDefaultSystemOutputDevice)
    let rows: [[String: Any]] = HAL.devices().compactMap { id in
        guard let count = HAL.channels(id), count > 0 else { return nil }
        let props: [[String: Any]] = ([0] + Array(1...count)).map { element in
            let mute = address(kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput, UInt32(element))
            let volume = address(kAudioDevicePropertyVolumeScalar, kAudioObjectPropertyScopeOutput, UInt32(element))
            let m: UInt32? = HAL.get(id, mute, initial: UInt32(0))
            let v: Float32? = HAL.get(id, volume, initial: Float32(-1))
            return ["element": element, "mute_readable": m != nil, "mute_writable": HAL.writable(id, mute),
                    "volume_readable": v != nil, "volume_writable": HAL.writable(id, volume)]
        }
        let transport: UInt32? = HAL.get(id, address(kAudioDevicePropertyTransportType), initial: UInt32(0))
        return ["device_id": id, "uid": HAL.uid(id) ?? "", "transport": transport ?? 0,
                "default_output": id == output, "default_system_output": id == system,
                "output_channels": count, "output_controls": props]
    }
    emit(["schema": 1, "event": "read_only_output_audit", "devices": rows, "input_changed": false])
}

func selfTest() {
    precondition(OutputPolicy.physical(kAudioDeviceTransportTypeBuiltIn))
    precondition(!OutputPolicy.physical(kAudioDeviceTransportTypeVirtual))
    precondition(!OutputPolicy.physical(kAudioDeviceTransportTypeAggregate))
    precondition(!OutputPolicy.physical(kAudioDeviceTransportTypeUnknown))
    precondition(OutputPolicy.volumeNeedsZero(0.5))
    precondition(!OutputPolicy.volumeNeedsZero(0))
    precondition(!OutputPolicy.volumeNeedsZero(.nan))
    precondition(!OutputPolicy.volumeNeedsZero(.infinity))
    precondition(!OutputPolicy.allChannelsProtected(0, []))
    precondition(!OutputPolicy.allChannelsProtected(2, [1]))
    precondition(OutputPolicy.allChannelsProtected(2, [1, 2]))
    precondition(!OutputPolicy.allChannelsProtected(65, Set(1...65)))
    precondition(OutputPolicy.routeFallback(isCurrent: true, isPhysical: true, confirmed: false, sinkFound: true, alreadySink: false))
    precondition(!OutputPolicy.routeFallback(isCurrent: false, isPhysical: true, confirmed: false, sinkFound: true, alreadySink: false))
    precondition(!OutputPolicy.routeFallback(isCurrent: true, isPhysical: false, confirmed: false, sinkFound: true, alreadySink: false))
    precondition(!OutputPolicy.routeFallback(isCurrent: true, isPhysical: true, confirmed: true, sinkFound: true, alreadySink: false))
    precondition(!OutputPolicy.routeFallback(isCurrent: true, isPhysical: true, confirmed: false, sinkFound: false, alreadySink: false))
    precondition(!OutputPolicy.routeFallback(isCurrent: true, isPhysical: true, confirmed: false, sinkFound: true, alreadySink: true))
    emit(["schema": 1, "event": "inert_policy_tests", "assertions": 18, "device_operations": 0])
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments == ["--self-test"] { selfTest() }
else if arguments == ["--audit"] { audit() }
else {
    // Closed CLI. Root supplies UIDs from a fresh --audit; no automatic name
    // discovery, shell commands, device resets, audio taps or guest operations.
    func value(_ flag: String) -> String? {
        guard let i = arguments.firstIndex(of: flag), i+1 < arguments.count else { return nil }
        let v = arguments[i+1]
        return !v.isEmpty && v.utf8.count <= 512 && !v.contains("\n") && !v.contains("\r") ? v : nil
    }
    let allowed = (arguments.first == "--once" || arguments.first == "--watch") &&
        (arguments.count == 3 && arguments[1] == "--speaker-uid"
         || arguments.count == 5 && arguments[1] == "--speaker-uid" && arguments[3] == "--sink-uid")
    guard allowed, let speaker = value("--speaker-uid"),
          arguments.count != 5 || value("--sink-uid") != nil,
          value("--sink-uid") != speaker else {
        emit(["schema": 1, "event": "invalid_closed_arguments", "device_operations": 0]); exit(2)
    }
    let guarder = OutputGuard(sinkUID: value("--sink-uid"), speakerUID: speaker)
    if arguments.first == "--once" {
        guarder.queue.sync { guarder.reconcile(watch: false) }
        exit(guarder.lastHealthy ? 0 : 3)
    } else {
        guarder.start()
        withExtendedLifetime(guarder) { CFRunLoopRun() }
    }
}
