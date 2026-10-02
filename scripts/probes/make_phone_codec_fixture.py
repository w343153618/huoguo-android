#!/usr/bin/env python3
"""Create private synthetic fixed-codec inputs, never a real video acceptance test."""
import argparse,hashlib,json,struct,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from scripts.probes.apple_ltr_fixture import Encoder,framed_stream

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--width',type=int,required=True);p.add_argument('--height',type=int,required=True);p.add_argument('--fps',type=int,choices=(60,120),default=60);p.add_argument('--bitrate',type=int,default=8000000);p.add_argument('--encoder',type=Path,required=True);p.add_argument('--fixture',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args()
 if not (a.width in (540,720,1080) and a.height in (960,1280,1920)):p.error('Bounded portrait fixture required')
 if not a.fixture.resolve().is_relative_to(Path('/private/tmp')) or a.fixture.exists():p.error('New private temporary fixture required')
 # One independent 2-second synthetic sequence, repeated ten times from IDR.
 frames=2*a.fps;cycles=10
 if a.fps==120:frames=120;cycles=20
 raw=subprocess.Popen(['/opt/homebrew/bin/ffmpeg','-v','error','-f','lavfi','-i',f'testsrc2=size={a.width}x{a.height}:rate={a.fps}','-frames:v',str(frames),'-pix_fmt','rgba','-f','rawvideo','pipe:1'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
 encoder=Encoder(a.encoder,False,False,a.fps,a.bitrate)
 try:
  size=a.width*a.height*4
  for index in range(frames):
   pixels=bytearray()
   while len(pixels)<size:
    part=raw.stdout.read(size-len(pixels))
    if not part:raise RuntimeError('synthetic_pixels_truncated')
    pixels.extend(part)
   pts=1_000_000+index*1_000_000//a.fps
   encoder.frame(pixels,a.width,a.height,pts);encoder.wait_frame(pts)
  raw.wait(timeout=15)
  if raw.returncode:raise RuntimeError('synthetic_generator_failed')
 finally:
  encoder.close()
  if raw.stdout:raw.stdout.close()
  if raw.poll() is None:
   raw.terminate()
   try:raw.wait(timeout=3)
   except subprocess.TimeoutExpired:raw.kill();raw.wait(timeout=3)
 if encoder.failure:raise RuntimeError('native_encoder_failed')
 media=[x for x in encoder.packets if not x['config']]
 if len(media)!=frames:raise RuntimeError('incomplete_encoded_fixture')
 ready=[x for x in encoder.events if x.get('event')=='ready']
 if not ready or ready[0].get('using_hardware') is not True:raise RuntimeError('hardware_readback_not_verified')
 data=framed_stream(encoder.packets,(a.width,a.height),a.fps,frames,cycles=cycles)
 a.fixture.write_bytes(data);a.fixture.chmod(0o600)
 d={'scope':'Synthetic testsrc2 encoder/phone capacity isolation; no actual YouTube, network or audio','geometry':[a.width,a.height],'source_fps':a.fps,'source_unique_frames':frames,'repeated_cycles':cycles,'media_records':frames*cycles,'duration_seconds':frames*cycles/a.fps,'fixture_bytes':len(data),'fixture_sha256':hashlib.sha256(data).hexdigest(),'encoder_sha256':hashlib.sha256(a.encoder.read_bytes()).hexdigest(),'hardware_using':True,'fixture_under_private_tmp':True}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(d,indent=2)+'\n');print(json.dumps(d),flush=True)
if __name__=='__main__':main()
