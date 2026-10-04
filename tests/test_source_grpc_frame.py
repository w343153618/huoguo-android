"""Owned files and fake unary transport only; never a device/lease/FPS test."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zlib

from scripts.probes import source_grpc_frame as m
from scripts.probes import source_frame_coordinator as coordinator


def deployment(folder):
    return dict(schema='owner-source-frame-reader-v1', emulator_pid=123, emulator_uid=m.OWNER_UID,
        emulator_lstart='Sun Oct  4 12:34:56 2026', emulator_argv_sha256='a'*64,
        output_png=str(Path(folder)/'source-frame.png'))


class FrameReaderTests(unittest.TestCase):
    def test_descriptor_construction_is_inert_and_scope_closed(self):
        with patch.object(m, 'read_regular', side_effect=AssertionError()), patch.object(m.subprocess, 'run', side_effect=AssertionError()):
            reader=m.Reader(deployment('/private/tmp/inert'))
            self.assertFalse(reader.used); self.assertFalse(reader.status['executed'])
        for change in ({'emulator_pid':True},{'emulator_uid':0},{'emulator_lstart':'old'},
                       {'output_png':'relative/source-frame.png'},{'output_png':'/tmp/x/../source-frame.png'},
                       {'grpc_token':'secret'},{'emulator_argv_sha256':'bad'}):
            value=deployment('/private/tmp/inert');value.update(change)
            with self.assertRaises(m.Rejected):m.Reader(value)

    def test_process_parse_exact_AVD_endpoint_start_and_no_raw_command_projection(self):
        command=sorted(m.EMULATORS)[0]+' -avd RemoteAndroid17Compare -grpc 8556 -grpc-use-token'
        row=' 123 501 Sun Oct  4 12:34:56 2026 '+command+'\n'
        value=m.process_identity(row,123)
        self.assertEqual(value['emulator_argv_sha256'],hashlib.sha256(command.encode()).hexdigest())
        self.assertNotIn('command',value)
        headless=sorted(m.EMULATORS)[1]+' @RemoteAndroid17Compare -grpc 8556 -grpc-use-token'
        self.assertEqual(m.process_identity(' 123 501 Sun Oct  4 12:34:56 2026 '+headless+'\n',123)['emulator_pid'],123)
        for bad in (row.replace(' 123 ',' 124 ',1),row.replace('501','0',1),
                    row.replace('8556','8557'),row.replace('RemoteAndroid17Compare','friend'),
                    row.replace(' -grpc 8556',' -grpc 8556 -grpc 8556'),row+'foreign\n',
                    row.replace('qemu-system-aarch64','adb'),row.replace(' -avd ', ' @RemoteAndroid17Compare -avd ')):
            with self.assertRaises(m.Rejected):m.process_identity(bad,123)

    def test_discovery_closed_endpoint_duplicate_and_error_redaction(self):
        good=b'avd.name=RemoteAndroid17Compare\ngrpc.port=8556\ngrpc.token=private-memory-token\n'
        self.assertEqual(m.discovery(good),'private-memory-token')
        for bad in (good+b'grpc.token=other\n',good.replace(b'8556',b'8557'),
                    good.replace(b'Compare',b'Other'),good+b'foreign_line',good+b'\xff'):
            with self.assertRaises(m.Rejected) as error:m.discovery(bad)
            self.assertNotIn('private-memory-token',str(error.exception))

    def test_exact_RGBA_png_preserves_rows_and_rejects_foreign_dimensions(self):
        raw=bytes([1,2,3,4])*(m.WIDTH*m.HEIGHT)
        png=m.rgba_png(m.WIDTH,m.HEIGHT,raw)
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        offset=8;body=b''
        while offset<len(png):
            size=struct.unpack('!I',png[offset:offset+4])[0];kind=png[offset+4:offset+8]
            part=png[offset+8:offset+8+size]
            self.assertEqual(struct.unpack('!I',png[offset+8+size:offset+12+size])[0],zlib.crc32(kind+part)&0xffffffff)
            if kind==b'IDAT':body+=part
            offset+=size+12
        rows=zlib.decompress(body)
        self.assertEqual(rows,b''.join(b'\0'+raw[i:i+m.WIDTH*4] for i in range(0,len(raw),m.WIDTH*4)))
        for dimensions,data in (((m.HEIGHT,m.WIDTH),raw),((m.WIDTH,m.HEIGHT),raw[:-1])):
            with self.assertRaises(m.Rejected):m.rgba_png(*dimensions,data)

    def fixture(self, mode='success', clock=lambda:1):
        folder=tempfile.TemporaryDirectory(prefix='frame-reader-fixture-');self.addCleanup(folder.cleanup)
        self.enterContext(patch.object(m,'OWNER_UID',os.getuid()))
        self.enterContext(patch.object(m.sys,'platform','darwin'))
        discovery=Path(folder.name)/'discovery';discovery.mkdir(mode=0o700)
        (discovery/'pid_123.ini').write_bytes(b'avd.name=RemoteAndroid17Compare\ngrpc.port=8556\ngrpc.token=only-memory\n')
        self.enterContext(patch.object(m,'DISCOVERY',discovery))
        reader=m.Reader(deployment(folder.name),clock=clock)
        before={k:v for k,v in reader.deployment.items() if k.startswith('emulator_')}
        processes=[dict(before),dict(before)]
        if mode=='process':processes[1]['emulator_lstart']='Sun Oct  4 12:34:57 2026'
        self.enterContext(patch.object(reader,'_process',side_effect=processes))
        calls=[]
        image=types.SimpleNamespace(format=types.SimpleNamespace(format=1,width=m.WIDTH,height=m.HEIGHT),image=b'X'*(m.WIDTH*m.HEIGHT*4))
        if mode=='RGBA':image.format.width=m.HEIGHT
        class Format:
            RGBA8888=1
            def __init__(self,**kwargs):calls.append(('request',kwargs))
        def rpc(request,*,timeout,metadata):
            calls.append(('rpc',timeout,metadata))
            if mode=='rpc':raise RuntimeError('sensitive RPC details only-memory')
            if mode=='discovery':
                (discovery/'pid_123.ini').write_bytes(b'avd.name=RemoteAndroid17Compare\ngrpc.port=8556\ngrpc.token=changed\n')
            return image
        pb=types.SimpleNamespace(ImageFormat=Format)
        service=types.SimpleNamespace(EmulatorControllerStub=lambda channel:types.SimpleNamespace(getScreenshot=rpc))
        self.enterContext(patch.object(reader,'_proto',return_value=dict(zip(m.PINS,(pb,service)))))
        class Channel:
            def close(self):
                calls.append(('close',))
                if mode=='close':raise RuntimeError('sensitive close failure')
        def channel(target,options):calls.append(('channel',target,options));return Channel()
        grpc=types.SimpleNamespace(insecure_channel=channel,channel_ready_future=lambda channel:
            types.SimpleNamespace(result=lambda timeout:calls.append(('ready',timeout))))
        self.enterContext(patch.dict(sys.modules,{'grpc':grpc}))
        self.enterContext(patch.object(m,'rgba_png',return_value=b'private-png-fixture'))
        return reader,Path(folder.name),calls

    def test_one_proxy_disabled_unary_closes_before_private_write(self):
        reader,folder,calls=self.fixture();ready={'image_width':540,'image_height':960}
        result=reader.observe(ready)
        self.assertEqual(result['rpc_calls'],1);self.assertEqual(result['stream_calls'],0)
        self.assertTrue(result['grpc_channel_closed']);self.assertNotIn('token',str(result))
        self.assertEqual(next(c for c in calls if c[0]=='channel')[1],'127.0.0.1:8556')
        self.assertIn(('grpc.enable_http_proxy',0),calls[0][2]);self.assertIn(('grpc.enable_retries',0),calls[0][2])
        self.assertEqual(len([c for c in calls if c[0]=='rpc']),1);self.assertLessEqual(next(c for c in calls if c[0]=='rpc')[1],3)
        self.assertEqual((folder/'source-frame.png').read_bytes(),b'private-png-fixture')
        self.assertEqual((folder/'source-frame.png').stat().st_mode & 0o777,0o600)
        coordinator.projection(result,ready)
        with self.assertRaisesRegex(m.Rejected,'no_retry'):reader.observe(ready)

    def test_remote_error_close_error_changed_identity_discovery_or_pixels_never_publish_file(self):
        for mode in ('rpc','close','process','discovery','RGBA'):
            with self.subTest(mode=mode):
                reader,folder,calls=self.fixture(mode)
                with self.assertRaises(m.Rejected) as error:reader.observe({'image_width':540,'image_height':960})
                self.assertEqual(str(error.exception),'source_frame_reader_failed')
                self.assertIn(('close',),calls);self.assertFalse((folder/'source-frame.png').exists())
                with self.assertRaisesRegex(m.Rejected,'no_retry'):reader.observe({'image_width':540,'image_height':960})

    def test_existing_symlink_or_file_is_never_overwritten(self):
        for kind in ('file','symlink'):
            reader,folder,calls=self.fixture();file=folder/'source-frame.png'
            if kind=='file':file.write_bytes(b'original')
            else:file.symlink_to(folder/'other')
            with self.assertRaises(m.Rejected):reader.observe({'image_width':540,'image_height':960})
            self.assertEqual(calls,[])
            if kind=='file':self.assertEqual(file.read_bytes(),b'original')
            else:self.assertTrue(file.is_symlink())

    def test_clock_exceeded_after_write_does_not_accept_private_partial_as_observation(self):
        count=[0]
        def clock():count[0]+=1;return 7_000_000_001 if count[0]>=7 else 1
        reader,folder,calls=self.fixture(clock=clock)
        with self.assertRaises(m.Rejected):reader.observe({'image_width':540,'image_height':960})
        self.assertTrue(reader.status['private_pixel_file_may_remain'])
        self.assertTrue((folder/'source-frame.png').exists());self.assertIn(('close',),calls)

    def test_private_descriptor_bound_duplicate_and_link_rejection(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(m,'OWNER_UID',os.getuid()):
            path=Path(folder)/'descriptor.json';path.write_text(json.dumps(deployment(folder)));path.chmod(0o600)
            self.assertEqual(m.read_deployment(path)['emulator_pid'],123)
            link=Path(folder)/'hard';os.link(path,link)
            with self.assertRaises(m.Rejected):m.read_deployment(path)
            link.unlink();path.write_text('{"schema":1,"schema":2}')
            with self.assertRaises(m.Rejected):m.read_deployment(path)
            path.write_text(' '*4097)
            with self.assertRaises(m.Rejected):m.read_deployment(path)

    def test_proto_changed_or_preloaded_cannot_be_executed(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(m,'OWNER_UID',os.getuid()),patch.object(m,'PROTO',Path(folder)):
            for name in m.PINS:(Path(folder)/(name+'.py')).write_text('raise AssertionError("not executed")')
            reader=m.Reader(deployment(folder))
            with self.assertRaisesRegex(m.Rejected,'pin'):reader._proto()

    def test_explicit_dependency_preflight_has_no_channel_token_or_pixel_action(self):
        reader,folder,calls=self.fixture()
        reader.preflight()
        self.assertEqual(calls,[]);self.assertFalse(reader.used)
        self.assertTrue(reader.status['local_dependencies_verified'])
        with patch.object(reader,'_proto',side_effect=ImportError('opaque host dependency')):
            with self.assertRaisesRegex(m.Rejected,'dependencies_unavailable'):reader.preflight()
        self.assertFalse((folder/'source-frame.png').exists())


if __name__=='__main__':unittest.main()
