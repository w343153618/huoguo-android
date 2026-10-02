// Synthetic SPS/PPS helper only. It opens no AVD, service, UI or media source.
#include <VideoToolbox/VideoToolbox.h>
#include <CoreMedia/CoreMedia.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void output(void *context,void *source,OSStatus status,VTDecodeInfoFlags flags,
                   CVImageBufferRef image,CMTime pts,CMTime duration) {
    (void)context;(void)source;(void)status;(void)flags;(void)image;(void)pts;(void)duration;
}
int main(int argc,char **argv) {
    if(argc!=3) return 2;
    const bool disable=strcmp(argv[2],"disable")==0,jpeg=strcmp(argv[2],"jpeg")==0;
    if(!disable && !jpeg && strcmp(argv[2],"default")!=0) return 2;
    CMVideoFormatDescriptionRef format=NULL; OSStatus format_status;
    if(jpeg) format_status=CMVideoFormatDescriptionCreate(kCFAllocatorDefault,kCMVideoCodecType_JPEG,320,240,NULL,&format);
    else {
        unsigned char bytes[4096]; size_t count;
        FILE *file=fopen(argv[1],"rb"); if(!file) return 2;
        count=fread(bytes,1,sizeof(bytes),file); const bool complete=feof(file); fclose(file);
        if(!complete || count<4) return 2;
        const size_t sps_size=((size_t)bytes[0]<<8)|bytes[1],pps_size=((size_t)bytes[2]<<8)|bytes[3];
        if(!sps_size || !pps_size || sps_size+pps_size+4!=count) return 2;
        const uint8_t *sets[2]={bytes+4,bytes+4+sps_size}; const size_t lengths[2]={sps_size,pps_size};
        format_status=CMVideoFormatDescriptionCreateFromH264ParameterSets(kCFAllocatorDefault,2,sets,lengths,4,&format);
        memset(bytes,0,sizeof(bytes));
    }
    if(format_status!=noErr || !format) { printf("{\"synthetic_driver\":1,\"format_status\":%d}\n",(int)format_status); return 1; }
    CFMutableDictionaryRef specification=NULL;
    if(disable) {
        specification=CFDictionaryCreateMutable(kCFAllocatorDefault,0,&kCFTypeDictionaryKeyCallBacks,&kCFTypeDictionaryValueCallBacks);
        if(!specification) { CFRelease(format); return 1; }
        CFDictionarySetValue(specification,kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder,kCFBooleanFalse);
    }
    VTDecompressionOutputCallbackRecord callback={output,NULL}; VTDecompressionSessionRef session=NULL;
    const OSStatus create_status=VTDecompressionSessionCreate(kCFAllocatorDefault,format,specification,NULL,&callback,&session);
    CFTypeRef value=NULL; OSStatus property_status=kVTPropertyNotSupportedErr; bool known=false,hardware=false;
    if(create_status==noErr && session) {
        property_status=VTSessionCopyProperty(session,kVTDecompressionPropertyKey_UsingHardwareAcceleratedVideoDecoder,kCFAllocatorDefault,&value);
        if(property_status==noErr && value && CFGetTypeID(value)==CFBooleanGetTypeID()) { known=true;hardware=CFBooleanGetValue((CFBooleanRef)value); }
    }
    const bool preserved=!specification ||
        (CFDictionaryGetValue(specification,kVTVideoDecoderSpecification_EnableHardwareAcceleratedVideoDecoder)==kCFBooleanFalse &&
         !CFDictionaryContainsKey(specification,kVTVideoDecoderSpecification_RequireHardwareAcceleratedVideoDecoder));
    printf("{\"synthetic_driver\":1,\"format_status\":%d,\"create_status\":%d,\"property_status\":%d,"
           "\"hardware_known\":%s,\"hardware\":%s,\"original_spec_preserved\":%s,\"jpeg\":%s}\n",
           (int)format_status,(int)create_status,(int)property_status,known?"true":"false",hardware?"true":"false",preserved?"true":"false",jpeg?"true":"false");
    if(value) CFRelease(value);
    if(session) { VTDecompressionSessionInvalidate(session); CFRelease(session); }
    if(specification) CFRelease(specification);
    CFRelease(format); return 0;
}
