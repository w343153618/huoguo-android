#include "phone_receiver.hpp"
#include <jni.h>
#include <unordered_map>

namespace {
using namespace huoguo::android_udp;
struct State { std::mutex lock; PhoneReceiver receiver; };
std::mutex registryLock;
std::unordered_map<jlong,std::shared_ptr<State>> registry;
jlong nextHandle=1;
void error(JNIEnv* env,const char* klass,const char* message) {
    auto type=env->FindClass(klass); if(type) { env->ThrowNew(type,message); env->DeleteLocalRef(type); }
}
std::shared_ptr<State> lookup(JNIEnv* env,jlong handle) {
    std::lock_guard guard(registryLock); auto it=registry.find(handle);
    if(it==registry.end()) { error(env,"java/lang/IllegalStateException","UDP receiver handle is closed"); return {}; }
    return it->second;
}
jobjectArray arrays(JNIEnv* env,const std::vector<Bytes>& result) {
    auto byteArray=env->FindClass("[B"); if(!byteArray) return nullptr;
    auto out=env->NewObjectArray(jsize(result.size()),byteArray,nullptr); env->DeleteLocalRef(byteArray);
    if(!out) return nullptr;
    for(size_t i=0;i<result.size();i++) {
        auto bytes=env->NewByteArray(jsize(result[i].size())); if(!bytes) return nullptr;
        env->SetByteArrayRegion(bytes,0,jsize(result[i].size()),reinterpret_cast<const jbyte*>(result[i].data()));
        env->SetObjectArrayElement(out,jsize(i),bytes); env->DeleteLocalRef(bytes);
        if(env->ExceptionCheck()) return nullptr;
    }
    return out;
}
template<class F> jobjectArray invoke(JNIEnv* env,jlong handle,jlong now,F fn) {
    if(now<=0) { error(env,"java/lang/IllegalArgumentException","arrivalUs must be monotonic and positive"); return nullptr; }
    auto state=lookup(env,handle); if(!state) return nullptr;
    try { std::lock_guard guard(state->lock); return arrays(env,fn(state->receiver,uint64_t(now))); }
    catch(const std::bad_alloc&) { error(env,"java/lang/OutOfMemoryError","bounded UDP receiver allocation"); }
    catch(const std::exception&) { error(env,"java/lang/IllegalArgumentException","invalid UDP receiver operation"); }
    return nullptr;
}
}
extern "C" JNIEXPORT jlong JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeCreate(JNIEnv* env,jclass) {
    try {
        std::lock_guard guard(registryLock);
        if(registry.size()>=8 || nextHandle==INT64_MAX) { error(env,"java/lang/IllegalStateException","UDP receiver capacity"); return 0; }
        const jlong handle=nextHandle++; registry.emplace(handle,std::make_shared<State>()); return handle;
    } catch(const std::bad_alloc&) { error(env,"java/lang/OutOfMemoryError","UDP receiver allocation"); }
    catch(const std::exception&) { error(env,"java/lang/IllegalStateException","UDP FEC initialization"); }
    return 0;
}
extern "C" JNIEXPORT jobjectArray JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeAccept(JNIEnv* env,jclass,jlong handle,jbyteArray packet,jlong arrival) {
    if(!packet || env->GetArrayLength(packet)<jsize(HeaderBytes) || env->GetArrayLength(packet)>jsize(HeaderBytes+ShardBytes)) {
        error(env,"java/lang/IllegalArgumentException","HGUD packet size"); return nullptr;
    }
    try {
        Bytes bytes(size_t(env->GetArrayLength(packet)));
        env->GetByteArrayRegion(packet,0,jsize(bytes.size()),reinterpret_cast<jbyte*>(bytes.data()));
        if(env->ExceptionCheck()) return nullptr;
        return invoke(env,handle,arrival,[&](PhoneReceiver& receiver,uint64_t now){return receiver.accept(bytes,now);});
    } catch(const std::bad_alloc&) { error(env,"java/lang/OutOfMemoryError","HGUD packet allocation"); return nullptr; }
}
extern "C" JNIEXPORT jobjectArray JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeExpire(JNIEnv* env,jclass,jlong handle,jlong now) {
    return invoke(env,handle,now,[](PhoneReceiver& receiver,uint64_t at){return receiver.expire(at);});
}
extern "C" JNIEXPORT jlongArray JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeStats(JNIEnv* env,jclass,jlong handle) {
    auto state=lookup(env,handle); if(!state) return nullptr;
    std::lock_guard guard(state->lock); const auto raw=state->receiver.stats(); std::array<jlong,21> values{};
    std::transform(raw.begin(),raw.end(),values.begin(),[](uint64_t value){return jlong(std::min(value,uint64_t(INT64_MAX)));});
    auto out=env->NewLongArray(jsize(values.size())); if(out) env->SetLongArrayRegion(out,0,jsize(values.size()),values.data()); return out;
}
extern "C" JNIEXPORT void JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeSetDiagnostics(JNIEnv* env,jclass,jlong handle,jboolean enabled) {
    auto state=lookup(env,handle);if(!state)return;std::lock_guard guard(state->lock);state->receiver.setDiagnostics(enabled==JNI_TRUE);
}
extern "C" JNIEXPORT void JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeSetMappingDiagnostics(JNIEnv* env,jclass,jlong handle,jboolean enabled) {
    auto state=lookup(env,handle);if(!state)return;std::lock_guard guard(state->lock);state->receiver.setMappingDiagnostics(enabled==JNI_TRUE);
}
extern "C" JNIEXPORT jlongArray JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeMappingDetails(JNIEnv* env,jclass,jlong handle) {
    auto state=lookup(env,handle);if(!state)return nullptr;std::lock_guard guard(state->lock);
    const auto raw=state->receiver.mappingDetails();std::array<jlong,PhoneReceiver::MappingDetailValues> values{};
    std::transform(raw.begin(),raw.end(),values.begin(),[](uint64_t value){return jlong(std::min(value,uint64_t(INT64_MAX)));});
    auto out=env->NewLongArray(jsize(values.size()));if(out)env->SetLongArrayRegion(out,0,jsize(values.size()),values.data());return out;
}
extern "C" JNIEXPORT jobjectArray JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeDrainEvents(JNIEnv* env,jclass,jlong handle) {
    auto state=lookup(env,handle);if(!state)return nullptr;
    try{
        std::lock_guard guard(state->lock);const auto events=state->receiver.drainEvents();
        auto klass=env->FindClass("[J");if(!klass)return nullptr;
        auto out=env->NewObjectArray(jsize(events.size()),klass,nullptr);env->DeleteLocalRef(klass);if(!out)return nullptr;
        for(size_t index=0;index<events.size();index++){
            std::array<jlong,14> row{};std::transform(events[index].begin(),events[index].end(),row.begin(),
                [](uint64_t value){return jlong(std::min(value,uint64_t(INT64_MAX)));});
            auto values=env->NewLongArray(jsize(row.size()));if(!values)return nullptr;
            env->SetLongArrayRegion(values,0,jsize(row.size()),row.data());env->SetObjectArrayElement(out,jsize(index),values);
            env->DeleteLocalRef(values);if(env->ExceptionCheck())return nullptr;
        }
        return out;
    }catch(const std::bad_alloc&){error(env,"java/lang/OutOfMemoryError","bounded UDP diagnostics allocation");}
    catch(const std::exception&){error(env,"java/lang/IllegalArgumentException","UDP diagnostics drain");}
    return nullptr;
}
extern "C" JNIEXPORT jlongArray JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeEventStats(JNIEnv* env,jclass,jlong handle) {
    auto state=lookup(env,handle);if(!state)return nullptr;std::lock_guard guard(state->lock);
    const auto raw=state->receiver.eventStats();std::array<jlong,4> values{};
    std::transform(raw.begin(),raw.end(),values.begin(),[](uint64_t value){return jlong(std::min(value,uint64_t(INT64_MAX)));});
    auto out=env->NewLongArray(jsize(values.size()));if(out)env->SetLongArrayRegion(out,0,jsize(values.size()),values.data());return out;
}
extern "C" JNIEXPORT void JNICALL Java_local_remoteandroid_direct_NativeUdpFec_nativeDestroy(JNIEnv*,jclass,jlong handle) {
    std::lock_guard guard(registryLock); registry.erase(handle);
}
