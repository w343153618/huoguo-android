package local.remoteandroid.direct;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.List;

/** Bounded synthetic fixture parser; contains no Android, networking or account code. */
final class FramedH264Fixture {
    static final int MAX_BYTES=32*1024*1024,MAX_RECORDS=4096,MAX_MEDIA_FRAMES=3601;
    static final long CONFIG=1L<<62,KEYFRAME=1L<<61,PTS_MASK=KEYFRAME-1;
    final byte[] bytes;
    final int width,height,mediaFrames;
    final long firstPtsUs,lastPtsUs,durationUs;
    final List<AccessUnit> units;

    private FramedH264Fixture(byte[] bytes,int width,int height,List<AccessUnit> units,
                              int frames,long first,long last,long duration) {
        this.bytes=bytes;this.width=width;this.height=height;this.units=units;mediaFrames=frames;
        firstPtsUs=first;lastPtsUs=last;durationUs=duration;
    }

    static FramedH264Fixture parse(byte[] bytes,int requestedFps)throws IOException {
        if(requestedFps!=60&&requestedFps!=120)throw new IOException("fixture_fps_invalid");
        if(bytes==null||bytes.length<16||bytes.length>MAX_BYTES)throw new IOException("fixture_length_invalid");
        ByteBuffer input=ByteBuffer.wrap(bytes).order(ByteOrder.BIG_ENDIAN);
        if(input.getInt()!=0x68323634||input.getInt()!=0x80000000)throw new IOException("fixture_header_invalid");
        int width=input.getInt(),height=input.getInt();
        if(width<16||height<16||width>4096||height>4096)throw new IOException("fixture_geometry_invalid");
        List<AccessUnit> units=new ArrayList<>();int frames=0;long first=-1,last=-1;
        while(input.hasRemaining()) {
            if(input.remaining()<12||units.size()>=MAX_RECORDS)throw new IOException("fixture_record_limit_or_truncated");
            long flags=input.getLong();int size=input.getInt();
            if(flags<0||size<5||size>8*1024*1024||size>input.remaining())throw new IOException("fixture_access_unit_invalid");
            int offset=input.position();
            boolean annexB=(bytes[offset]==0&&bytes[offset+1]==0&&
                (bytes[offset+2]==1||(bytes[offset+2]==0&&bytes[offset+3]==1)));
            if(!annexB)throw new IOException("fixture_annexb_required");
            long pts=flags&PTS_MASK;boolean config=(flags&CONFIG)!=0;
            if(pts>Long.MAX_VALUE/1000L)throw new IOException("fixture_pts_overflow");
            if(!config) {
                if(frames>=MAX_MEDIA_FRAMES||last>=0&&pts<=last)throw new IOException("fixture_media_pts_or_count_invalid");
                if(first<0)first=pts;
                if(pts-first>30_000_000L)throw new IOException("fixture_duration_exceeds_30_seconds");
                last=pts;frames++;
            } else if(frames>0)throw new IOException("fixture_reconfiguration_not_supported");
            units.add(new AccessUnit(flags,offset,size));input.position(offset+size);
        }
        if(frames<2)throw new IOException("fixture_media_missing");
        long duration=last-first+(last-first)/(frames-1);
        if(duration<999_000L||duration>30_001_000L)throw new IOException("fixture_duration_outside_1_to_30_seconds");
        return new FramedH264Fixture(bytes,width,height,units,frames,first,last,duration);
    }

    static final class AccessUnit {
        final long flags,ptsUs;final int offset,size;final boolean config,keyframe;
        AccessUnit(long flags,int offset,int size) {
            this.flags=flags;ptsUs=flags&PTS_MASK;this.offset=offset;this.size=size;
            config=(flags&CONFIG)!=0;keyframe=(flags&KEYFRAME)!=0;
        }
    }
}
