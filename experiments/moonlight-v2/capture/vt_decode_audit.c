// Isolated macOS arm64 audit. No constructor loads/injects another process.
#include <VideoToolbox/VideoToolbox.h>
#include <CoreMedia/CoreMedia.h>
#include <CoreFoundation/CoreFoundation.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#if !defined(__APPLE__) || !defined(__aarch64__)
#error "This audit library supports macOS arm64 only"
#endif

// Apple describes the __DATA,__interpose pair in dyld-interposing.h. Our SDK
// does not ship that header, so construct the equivalent Mach-O record here.
// Importantly, dyld does not redirect the replacee binding from this image;
// Apple's example calls the original symbol directly inside its replacement.
// https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld-interposing.h
#define DYLD_INTERPOSE(replacement_function, original_function) \
    __attribute__((used,section("__DATA,__interpose,interposing"))) \
    static const struct { const void *replacement; const void *original; } \
    huoguo_interpose_record = { (const void *)(uintptr_t)&replacement_function, \
                               (const void *)(uintptr_t)&original_function }

static pthread_once_t policy_once=PTHREAD_ONCE_INIT;
static bool require_hardware=false;
static _Atomic uint64_t ordinal=0;
static _Thread_local bool in_audit=false;

static void initialize_policy(void) {
    // Read one named opt-in only; no environment values are ever logged.
    const char *value=getenv("HUOGUO_REQUIRE_HW_DECODE");
    require_hardware=value && strcmp(value,"1")==0;
}

static void log_creation(uint64_t number, bool requested, bool applied,
                         OSStatus prepare_status, OSStatus create_status,
                         bool created, OSStatus property_status,
                         bool property_boolean, bool hardware) {
    // Each value is explicitly numeric or boolean. No media, dictionaries,
    // paths, environment, decoder names, session pointers, or account data.
    char line[512];
    const int length=snprintf(line,sizeof(line),
        "{\"vt_h264_create_audit\":1,\"ordinal\":%llu,\"require_requested\":%s,"
        "\"require_applied\":%s,\"force_prepare_status\":%d,\"create_status\":%d,"
        "\"session_created\":%s,\"property_read_status\":%d,\"property_is_boolean\":%s,"
        "\"hardware_known\":%s,\"hardware\":%s}\n",
        (unsigned long long)number,requested?"true":"false",applied?"true":"false",
        (int)prepare_status,(int)create_status,created?"true":"false",(int)property_status,
        property_boolean?"true":"false",property_boolean?"true":"false",hardware?"true":"false");
    if(length>0 && (size_t)length<sizeof(line)) {
        // A single short write keeps records together on a pipe. Audit output
        // failures do not change the decoder's return status or caller state.
        const ssize_t ignored=write(STDERR_FILENO,line,(size_t)length); (void)ignored;
    }
}

static OSStatus huoguo_audit_VTDecompressionSessionCreate(
    CFAllocatorRef allocator, CMVideoFormatDescriptionRef format,
    CFDictionaryRef specification, CFDictionaryRef image_attributes,
    const VTDecompressionOutputCallbackRecord *callback,
    VTDecompressionSessionRef *session_out) {
    // This import is the original framework function for this dylib's image.
    // Do not resolve RTLD_DEFAULT, whose symbol may be the replacement itself.
    if(in_audit || !format || CMFormatDescriptionGetMediaType(format)!=kCMMediaType_Video ||
       CMFormatDescriptionGetMediaSubType(format)!=kCMVideoCodecType_H264) {
        return VTDecompressionSessionCreate(allocator,format,specification,image_attributes,callback,session_out);
    }
    in_audit=true;
    pthread_once(&policy_once,initialize_policy);
    const uint64_t number=atomic_fetch_add_explicit(&ordinal,1,memory_order_relaxed)+1;
    CFMutableDictionaryRef changed=NULL;
    CFDictionaryRef effective=specification;
    OSStatus prepare_status=noErr;
    bool applied=false;
    if(require_hardware) {
        if(specification && CFGetTypeID(specification)!=CFDictionaryGetTypeID()) {
            // Only the explicit force path validates/prepares this copy. Do
            // not reinterpret a malformed CF object as a dictionary, or
            // silently bypass the user's force request if preparation fails.
            prepare_status=paramErr;
        } else {
            changed=specification ? CFDictionaryCreateMutableCopy(kCFAllocatorDefault,0,specification) :
                CFDictionaryCreateMutable(kCFAllocatorDefault,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
            if(changed) {
                CFDictionarySetValue(changed,kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder,kCFBooleanTrue);
                effective=changed; applied=true;
            } else prepare_status=kVTAllocationFailedErr;
        }
    }
    OSStatus status;
    if(require_hardware && prepare_status!=noErr) {
        // An explicit require request must not silently fall back on allocation
        // failure. Default audit-only never follows this branch.
        if(session_out) *session_out=NULL;
        status=prepare_status;
    } else {
        // Default mode sends every original argument, including the dictionary
        // pointer and allocator, unchanged. Opt-in changes only the copied spec.
        status=VTDecompressionSessionCreate(allocator,format,effective,image_attributes,callback,session_out);
    }
    if(changed) CFRelease(changed);
    const bool created=status==noErr && session_out && *session_out;
    CFTypeRef value=NULL;
    OSStatus property_status=kVTPropertyNotSupportedErr;
    bool property_boolean=false,hardware=false;
    if(created) {
        property_status=VTSessionCopyProperty(*session_out,kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,
                                              kCFAllocatorDefault,&value);
        if(property_status==noErr && value && CFGetTypeID(value)==CFBooleanGetTypeID()) {
            property_boolean=true; hardware=CFBooleanGetValue((CFBooleanRef)value);
        }
    }
    if(value) CFRelease(value);
    log_creation(number,require_hardware,applied,prepare_status,status,created,property_status,property_boolean,hardware);
    in_audit=false;
    return status;
}

DYLD_INTERPOSE(huoguo_audit_VTDecompressionSessionCreate,VTDecompressionSessionCreate);
