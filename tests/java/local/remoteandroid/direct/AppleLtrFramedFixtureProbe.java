package local.remoteandroid.direct;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;

/** Parses private synthetic LTR fixtures only; no Android codec or device runs. */
public final class AppleLtrFramedFixtureProbe {
    public static void main(String[] args)throws Exception {
        if(args.length==0)throw new IllegalArgumentException("Provide retained .h264framed fixture paths");
        for(String input:args) {
            Path path=Paths.get(input);
            FramedH264Fixture fixture=FramedH264Fixture.parse(Files.readAllBytes(path),60);
            String name=path.getFileName().toString();
            int cycles=name.contains("-20cycle")?20:1;
            int expected=(name.contains("-recovery-")?56:72)*cycles;
            if(fixture.mediaFrames!=expected||fixture.width!=540||fixture.height!=960)
                throw new AssertionError("Unexpected geometry/count: "+name);
            int configs=0,keys=0;
            for(FramedH264Fixture.AccessUnit unit:fixture.units) {
                if(unit.config)configs++;
                if(unit.keyframe)keys++;
            }
            if(configs!=1||keys<cycles||fixture.durationUs<1_000_000)
                throw new AssertionError("Unexpected config/key/duration: "+name);
            System.out.println(name+" PASS media="+fixture.mediaFrames+" duration_us="+
                fixture.durationUs+" config="+configs+" keyframe="+keys);
        }
    }
}
