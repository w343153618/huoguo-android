package local.remoteandroid.direct;

import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioTimestamp;
import android.media.AudioTrack;
import android.media.MediaCodec;
import android.media.MediaFormat;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.json.JSONObject;

/** Experimental UDP AAC receiver. No audio payload/logging is persisted.
 * UDP receive performs bounded parsing/queue admission only. Decoder and PCM
 * scheduling are separate workers sharing the VIDEO PlaybackClock mapping.
 */
final class UdpAudioReceiver implements AutoCloseable {
    private final MainActivity activity;private final int generation;
    private final UdpAudioAssembler assembler=new UdpAudioAssembler();
    private final ArrayBlockingQueue<UdpAudioAssembler.Frame> queue=new ArrayBlockingQueue<>(8);
    private final Thread worker;private volatile Thread drain;
    private volatile boolean closed;private volatile MediaCodec decoder;private volatile AudioTrack output;
    private volatile String failure="",decoderName="";private volatile boolean configured;
    private byte[] config;private long lastInputPts=-1;private volatile long lastPlaybackFrames;
    private final AtomicLong queueDrops=new AtomicLong(),lateDrops=new AtomicLong(),beforeConfig=new AtomicLong(),
        invalidConfigs=new AtomicLong(),configurations=new AtomicLong(),inputFrames=new AtomicLong(),pcmBytes=new AtomicLong(),
        timestampObservations=new AtomicLong(),validTimestamps=new AtomicLong(),pcmLateDrops=new AtomicLong();

    UdpAudioReceiver(MainActivity activity,int generation){
        this.activity=activity;this.generation=generation;
        worker=new Thread(this::decode,"udp-audio-input");worker.start();
    }
    private boolean active(){return !closed&&activity.running&&activity.generation==generation;}
    synchronized void accept(byte[] plaintext,long nowNs){
        if(closed)return;assembler.accept(plaintext,nowNs);admit(assembler.poll(nowNs));
    }
    synchronized void poll(long nowNs){if(!closed)admit(assembler.poll(nowNs));}
    private void admit(List<UdpAudioAssembler.Frame> frames){
        for(UdpAudioAssembler.Frame frame:frames){
            if(!queue.offer(frame)){
                // Configuration is needed to decode future data. Keep its
                // admission bounded; evict oldest queued media rather than wait.
                if(frame.config){queue.poll();queueDrops.incrementAndGet();if(!queue.offer(frame))queueDrops.incrementAndGet();}
                else queueDrops.incrementAndGet();
            }
        }
    }
    private void decode(){
        try{
            while(active()){
                UdpAudioAssembler.Frame frame=queue.poll(20,TimeUnit.MILLISECONDS);if(frame==null)continue;
                if(System.nanoTime()-frame.firstArrivalNs>=UdpAudioAssembler.DEADLINE_NS){lateDrops.incrementAndGet();continue;}
                if(frame.config){
                    if(!UdpAudioAssembler.supportedConfig(frame.data)){invalidConfigs.incrementAndGet();continue;}
                    if(!Arrays.equals(config,frame.data))configure(frame.data);
                    continue;
                }
                MediaCodec current=decoder;if(current==null){beforeConfig.incrementAndGet();continue;}
                if(frame.ptsUs<=lastInputPts){lateDrops.incrementAndGet();continue;}
                activity.playback.observeAudio(frame.ptsUs,frame.completeNs);
                long deadline=frame.firstArrivalNs+UdpAudioAssembler.DEADLINE_NS;int index=-1;
                while(active()&&decoder==current&&System.nanoTime()<deadline){index=current.dequeueInputBuffer(1000);if(index>=0)break;}
                if(index<0){lateDrops.incrementAndGet();continue;}
                ByteBuffer input=current.getInputBuffer(index);
                if(input==null||input.capacity()<frame.data.length)throw new IOException("audio_input_capacity");
                input.clear();input.put(frame.data);current.queueInputBuffer(index,0,frame.data.length,frame.ptsUs,0);
                inputFrames.incrementAndGet();lastInputPts=frame.ptsUs;
            }
        }catch(InterruptedException stop){Thread.currentThread().interrupt();}
        catch(Throwable error){if(active())failure=error.getClass().getSimpleName();}
    }
    private void configure(byte[] asc)throws Exception{
        releaseCurrent();if(!active())return;
        MediaFormat format=MediaFormat.createAudioFormat("audio/mp4a-latm",48000,2);
        format.setInteger(MediaFormat.KEY_PCM_ENCODING,AudioFormat.ENCODING_PCM_16BIT);
        format.setByteBuffer("csd-0",ByteBuffer.wrap(asc));
        MediaCodec next=MediaCodec.createDecoderByType("audio/mp4a-latm");
        AudioTrack nextOutput=null;
        try{
            next.configure(format,null,null,0);next.start();
            int min=AudioTrack.getMinBufferSize(48000,AudioFormat.CHANNEL_OUT_STEREO,AudioFormat.ENCODING_PCM_16BIT);
            if(min<=0)throw new IOException("audio_min_buffer");
            nextOutput=new AudioTrack.Builder().setAudioFormat(new AudioFormat.Builder().setSampleRate(48000)
                .setChannelMask(AudioFormat.CHANNEL_OUT_STEREO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build()).setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(Math.max(min,4096)).setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY).build();
            if(nextOutput.getState()!=AudioTrack.STATE_INITIALIZED)throw new IOException("audio_track_not_initialized");
            nextOutput.setVolume(activity.soundEnabled?1f:0f);nextOutput.play();
            decoder=next;output=nextOutput;activity.audio=next;activity.track=nextOutput;
            config=asc.clone();configured=true;lastInputPts=-1;decoderName=next.getName();configurations.incrementAndGet();
            int epoch=activity.presentationMetrics.resetAudio();
            MediaCodec owned=next;AudioTrack track=nextOutput;
            drain=new Thread(()->render(owned,track,epoch),"udp-audio-output");drain.start();
        }catch(Throwable error){
            if(nextOutput!=null){try{nextOutput.release();}catch(Exception ignored){}}
            try{next.stop();}catch(Exception ignored){}try{next.release();}catch(Exception ignored){}
            throw error;
        }
    }
    private void render(MediaCodec current,AudioTrack track,int epoch){
        AudioTimestamp stamp=new AudioTimestamp();MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
        long writtenFrames=0;ByteBuffer amplified=ByteBuffer.allocateDirect(16384);
        try{
            while(active()&&decoder==current){
                int index=current.dequeueOutputBuffer(info,10000);if(index<0)continue;
                try{
                    if(info.size==0)continue;
                    ByteBuffer pcm=current.getOutputBuffer(index);if(pcm==null||info.size%4!=0)throw new IOException("audio_pcm_alignment");
                    pcm.position(info.offset);pcm.limit(info.offset+info.size);
                    if(activity.audioGain!=1f){if(amplified.capacity()<pcm.remaining())amplified=ByteBuffer.allocateDirect(pcm.remaining());
                        PcmGain.process(pcm,amplified,activity.audioGain);pcm=amplified;}
                    long target=activity.playback.audioDeadline(info.presentationTimeUs);
                    // A frame arriving too late must not increase playout delay
                    // forever. Dropping this PCM record is bounded and explicit.
                    if(System.nanoTime()-target>80_000_000L){pcmLateDrops.incrementAndGet();continue;}
                    while(active()&&decoder==current){
                        long now=System.nanoTime();boolean valid=track.getTimestamp(stamp);
                        timestampObservations.incrementAndGet();if(valid)validTimestamps.incrementAndGet();
                        activity.presentationMetrics.audioTimestamp(epoch,valid,stamp.framePosition,stamp.nanoTime,now,
                            track.getPlayState()==AudioTrack.PLAYSTATE_PLAYING);
                        MediaPresentationMetrics.AudioEstimate estimate=activity.presentationMetrics.audioEstimate(now);
                        long tail=valid&&estimate.valid&&track.getPlayState()==AudioTrack.PLAYSTATE_PLAYING
                            ?AudioSubmissionClock.timestampQueueTailNs(now,stamp.nanoTime,writtenFrames,estimate.timestampFramePosition,48000)
                            :AudioSubmissionClock.UNAVAILABLE;
                        if(tail==AudioSubmissionClock.UNAVAILABLE){long queued=Math.max(0,writtenFrames-(track.getPlaybackHeadPosition()&0xffffffffL));
                            tail=now+queued*1_000_000_000L/48000L+25_000_000L;}
                        long wait=target-tail;if(wait<=2_000_000L)break;
                        Thread.sleep(Math.min(10,Math.max(1,wait/1_000_000L)));
                    }
                    long chunkFrames=0,writeDeadline=System.nanoTime()+80_000_000L;
                    while(pcm.hasRemaining()&&active()&&decoder==current){
                        int count=track.write(pcm,pcm.remaining(),AudioTrack.WRITE_NON_BLOCKING);
                        if(count<0||count%4!=0)throw new IOException("audio_write");
                        if(count==0){if(System.nanoTime()>writeDeadline){pcmLateDrops.incrementAndGet();break;}Thread.sleep(1);continue;}
                        long frames=count/4;
                        activity.presentationMetrics.audioWritten(epoch,writtenFrames,frames,info.presentationTimeUs+chunkFrames*1_000_000L/48000L);
                        writtenFrames+=frames;chunkFrames+=frames;pcmBytes.addAndGet(count);activity.audioOutputBytes.addAndGet(count);
                    }
                    lastPlaybackFrames=track.getPlaybackHeadPosition()&0xffffffffL;
                }finally{if(decoder==current)current.releaseOutputBuffer(index,false);}
            }
        }catch(InterruptedException stop){Thread.currentThread().interrupt();}
        catch(Throwable error){if(active()&&decoder==current)failure=error.getClass().getSimpleName();}
    }
    synchronized JSONObject snapshot()throws Exception{
        return new JSONObject().put("protocol","HGUA_AAC_over_authenticated_UDP").put("configuration_received",configured)
            .put("codec",decoderName).put("decoder_configurations",configurations.get()).put("accepted_fragments",assembler.acceptedFragments)
            .put("malformed",assembler.malformed).put("duplicates",assembler.duplicate).put("assembly_expired",assembler.expired)
            .put("assembly_evicted",assembler.evicted).put("complete_records",assembler.completed).put("reorder_drops",assembler.reorderDrops)
            .put("pending_records",assembler.pendingCount()).put("pending_complete_records",assembler.readyCount())
            .put("worker_queue_drops",queueDrops.get()).put("worker_late_drops",lateDrops.get()).put("media_before_configuration",beforeConfig.get())
            .put("unsupported_configurations",invalidConfigs.get()).put("decoder_input_records",inputFrames.get()).put("pcm_written_bytes",pcmBytes.get())
            .put("pcm_late_drops",pcmLateDrops.get()).put("timestamp_observations",timestampObservations.get()).put("valid_audio_timestamps",validTimestamps.get())
            .put("last_playback_head_frames",lastPlaybackFrames).put("failure_class",failure)
            .put("actual_acoustic_output_measured",false).put("actual_lip_sync_measured",false);
    }
    boolean hasDecodedAudio(){return pcmBytes.get()>0;}
    private void releaseCurrent(){
        MediaCodec old=decoder;AudioTrack oldTrack=output;decoder=null;output=null;
        if(activity.audio==old)activity.audio=null;if(activity.track==oldTrack)activity.track=null;
        Thread previous=drain;if(previous!=null&&previous!=Thread.currentThread()){previous.interrupt();try{previous.join(300);}catch(InterruptedException ignored){}}
        if(oldTrack!=null){try{lastPlaybackFrames=oldTrack.getPlaybackHeadPosition()&0xffffffffL;}catch(Exception ignored){}
            try{oldTrack.pause();oldTrack.flush();oldTrack.stop();}catch(Exception ignored){}try{oldTrack.release();}catch(Exception ignored){}}
        if(old!=null){try{old.stop();}catch(Exception ignored){}try{old.release();}catch(Exception ignored){}}
    }
    public void close(){closed=true;worker.interrupt();try{worker.join(500);}catch(InterruptedException ignored){}
        queue.clear();releaseCurrent();if(config!=null)Arrays.fill(config,(byte)0);}
}
