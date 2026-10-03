"""Offline checks of the actual experimental codec startup state machine.

Compiles its pure Java source and the actual nested Inbox types. Android-facing
Activity fields and unused JSON signatures are inert substitutes. No Android
SDK, codecs, sockets, host services, accounts, or phones are used; fixture
results are not device timing or performance acceptance.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'experiments/nps-transport/phone/CodecStartupGate.java'
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')

HARNESS = r'''
package local.remoteandroid.direct;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;

/** Calls the real gate; supplies timestamps and order without simulating a codec. */
public final class CodecStartupContractCheck {
    static final long MS=1_000_000L, T=1_000_000_000L, READY=T+250*MS;
    static final int W=1080,H=1920,BODY=1024;
    static void check(boolean value,String name){if(!value)throw new AssertionError(name);}
    static long stat(CodecStartupGate gate,String name){
        long[] values=gate.snapshot();
        for(int i=0;i<CodecStartupGate.STAT_NAMES.length;i++)if(CodecStartupGate.STAT_NAMES[i].equals(name))return values[i];
        throw new AssertionError("unknown startup numeric field "+name);
    }
    static int admit(CodecStartupGate gate,long pts,long received,boolean idr,long now){
        return gate.admission(W,H,pts,received,idr,BODY,now);
    }
    static CodecStartupGate preparing()throws Exception{
        CodecStartupGate gate=new CodecStartupGate(true,T);
        check(admit(gate,100,T+20*MS,true,T+30*MS)==CodecStartupGate.PREPARE,"bootstrap metadata accepted");
        check(gate.phase()==CodecStartupGate.PREPARING,"preparing phase");
        check(gate.preparing(100,W,H),"selected bootstrap identity");
        gate.preparationStarted(100,T+30*MS);
        return gate;
    }
    static CodecStartupGate ready()throws Exception{
        CodecStartupGate gate=preparing();
        check(gate.prepared(100,W,H,READY),"valid decoder-ready transition");
        check(gate.phase()==CodecStartupGate.WAIT_FRESH_IDR,"wait for fresh IDR");
        return gate;
    }
    static CodecStartupGate pending()throws Exception{
        CodecStartupGate gate=ready();
        check(admit(gate,200,READY+10*MS,true,READY+20*MS)==CodecStartupGate.ADMIT,"fresh IDR accepted");
        check(gate.phase()==CodecStartupGate.CHAIN_PENDING,"commit pending");
        return gate;
    }
    static void failsBy(CodecStartupGate gate,long now)throws Exception{
        try{gate.checkTimeout(now);}catch(Exception expected){}
        check(gate.phase()==CodecStartupGate.FAILED,"bounded wait failed");
        check(gate.failureCode()!=0,"failure reason retained");
    }
    static void legacy()throws Exception{
        CodecStartupGate gate=new CodecStartupGate(false,T);
        check(gate.phase()==CodecStartupGate.DISABLED,"opt-in disabled");
        check(admit(gate,1,T+10*MS,false,T+20*MS)==CodecStartupGate.ADMIT,"legacy P untouched");
        check(admit(gate,2,T+10*MS,true,T+200*MS)==CodecStartupGate.ADMIT,"legacy age policy delegated");
        check(!gate.requestDue(T+500*MS),"no new recovery requests while disabled");
        gate.close(T+600*MS);
        check(admit(gate,3,T+700*MS,true,T+710*MS)==CodecStartupGate.DROP,"closed disabled gate still closed");
    }
    static void bootstrap()throws Exception{
        CodecStartupGate gate=new CodecStartupGate(true,T);
        check(admit(gate,1,T+10*MS,false,T+20*MS)==CodecStartupGate.DROP,"P/configless IDR rejected");
        check(admit(gate,2,T+10*MS,true,T+90*MS)==CodecStartupGate.DROP,"80ms old bootstrap rejected");
        check(admit(gate,3,T+100*MS,true,T+99*MS)==CodecStartupGate.DROP,"future arrival rejected");
        check(gate.phase()==CodecStartupGate.WAIT_BOOTSTRAP,"invalid bootstrap cannot prepare");
        check(admit(gate,100,T+110*MS,true,T+120*MS)==CodecStartupGate.PREPARE,"valid bootstrap metadata only");
        for(int i=0;i<100;i++)check(admit(gate,101+i,T+130*MS,false,T+131*MS)==CodecStartupGate.DROP,"prepare P discarded");
        check(admit(gate,500,T+140*MS,true,T+141*MS)==CodecStartupGate.DROP,"prepare IDR not queued");
        check(gate.phase()==CodecStartupGate.PREPARING,"burst cannot open chain");
        check(!gate.requestDue(T+150*MS),"no pre-ready keyframe budget consumption");
        check(!gate.prepared(99,W,H,READY),"old bootstrap completion rejected");
        check(!gate.prepared(100,720,1280,READY),"wrong decoder geometry rejected");
        check(gate.prepared(100,W,H,READY),"selected completion accepted");
        check(admit(gate,100,T+110*MS,true,READY+MS)==CodecStartupGate.DROP,"bootstrap AU never becomes fresh");
    }
    static void freshness()throws Exception{
        CodecStartupGate gate=ready();
        check(admit(gate,200,READY-MS,true,READY+MS)==CodecStartupGate.DROP,"received before ready rejected");
        check(admit(gate,100,READY+MS,true,READY+2*MS)==CodecStartupGate.DROP,"same bootstrap PTS rejected");
        check(admit(gate,200,READY+MS,false,READY+2*MS)==CodecStartupGate.DROP,"P after ready still blocked");
        check(admit(gate,200,READY+MS,true,READY+81*MS)==CodecStartupGate.DROP,"exact 80ms age rejected");
        check(admit(gate,200,READY+90*MS,true,READY+89*MS)==CodecStartupGate.DROP,"fresh future arrival rejected");
        check(admit(gate,201,READY+100*MS,true,READY+180*MS-1)==CodecStartupGate.ADMIT,"just below age limit accepted");
        check(gate.phase()==CodecStartupGate.CHAIN_PENDING,"only valid fresh IDR opens pending admission");
        CodecStartupGate changed=ready();
        check(changed.admission(720,1280,200,READY+MS,true,BODY,READY+2*MS)==CodecStartupGate.DROP,"fresh wrong geometry rejected");
        check(changed.phase()==CodecStartupGate.FAILED&&changed.failureCode()==CodecStartupGate.GEOMETRY_CHANGED,
            "geometry change explicitly fails instead of using mismatched decoder");
    }
    static void commitLoss()throws Exception{
        CodecStartupGate gate=pending();
        check(admit(gate,201,READY+21*MS,false,READY+22*MS)==CodecStartupGate.ADMIT,"P may wait behind selected fresh IDR");
        gate.committed(199,READY+30*MS);
        check(gate.phase()==CodecStartupGate.CHAIN_PENDING,"unselected input completion ignored");
        gate.chainLost(READY+31*MS);
        check(gate.phase()==CodecStartupGate.WAIT_FRESH_IDR,"config success then AU failure closes chain");
        gate.committed(200,READY+32*MS);
        check(gate.phase()==CodecStartupGate.WAIT_FRESH_IDR,"lost epoch cannot be revived by stale commit");
        check(admit(gate,202,READY+33*MS,false,READY+34*MS)==CodecStartupGate.DROP,"P blocked after chain loss");
        check(admit(gate,300,READY+40*MS,true,READY+41*MS)==CodecStartupGate.ADMIT,"new complete recovery accepted");
        gate.committed(200,READY+42*MS);
        check(gate.phase()==CodecStartupGate.CHAIN_PENDING,"prior candidate cannot commit newer chain");
        gate.committed(300,READY+43*MS);
        check(gate.phase()==CodecStartupGate.STREAMING,"selected fresh input commit opens stream");
        check(admit(gate,301,READY+44*MS,false,READY+45*MS)==CodecStartupGate.ADMIT,"streaming preserves existing admission");
        long first=stat(gate,"committed_ns");gate.committed(400,READY+50*MS);
        check(stat(gate,"committed_ns")==first,"steady IDR does not rewrite first startup commit time");
        check(first>stat(gate,"fresh_received_ns")&&stat(gate,"fresh_received_ns")>=stat(gate,"ready_ns"),
            "startup report timing order retained");
        gate.close(READY+51*MS);
        check(stat(gate,"phase_before_close")==CodecStartupGate.STREAMING,"completed startup retained after close");
    }
    static void retries()throws Exception{
        CodecStartupGate gate=ready();
        check(gate.requestDue(READY),"ready requests first keyframe");
        check(!gate.requestDue(READY+499*MS),"500ms request spacing");
        check(gate.requestDue(READY+500*MS),"second bounded request");
        check(!gate.requestDue(READY+1499*MS),"1000ms second spacing");
        check(gate.requestDue(READY+1500*MS),"third bounded request");
        check(!gate.requestDue(READY+2000*MS),"no fourth request");
        failsBy(gate,READY+2500*MS);
        check(!gate.requestDue(READY+2501*MS),"failed gate sends no request");
        check(admit(gate,500,READY+2502*MS,true,READY+2503*MS)==CodecStartupGate.DROP,"failed gate rejects late recovery");
    }
    static void timeoutClose()throws Exception{
        CodecStartupGate bootstrap=new CodecStartupGate(true,T);
        failsBy(bootstrap,T+5000*MS);
        CodecStartupGate preparing=preparing();
        failsBy(preparing,T+30*MS+2500*MS);
        check(!preparing.prepared(100,W,H,T+2531*MS),"late configure completion cannot resurrect failure");
        CodecStartupGate gate=pending();
        gate.close(READY+25*MS);
        check(gate.phase()==CodecStartupGate.CLOSED,"close retained");
        gate.committed(200,READY+30*MS);gate.chainLost(READY+31*MS);
        check(!gate.prepared(100,W,H,READY+32*MS),"closed completion rejected");
        check(gate.phase()==CodecStartupGate.CLOSED,"close cannot revive by completion/loss");
        check(admit(gate,300,READY+33*MS,true,READY+34*MS)==CodecStartupGate.DROP,"close blocks subsequent arrivals");
        CodecStartupGate next=new CodecStartupGate(true,T+10_000*MS);
        check(next.phase()==CodecStartupGate.WAIT_BOOTSTRAP,"reconnect state independently initialized");
        check(admit(next,100,T+10_010*MS,true,T+10_011*MS)==CodecStartupGate.PREPARE,"reconnect can reuse source PTS independently");
        CodecStartupGate lateCommit=pending();lateCommit.committed(200,READY+2500*MS);
        check(lateCommit.phase()==CodecStartupGate.FAILED&&lateCommit.failureCode()==CodecStartupGate.FRESH_IDR_TIMEOUT,
            "late codec completion cannot bypass fresh startup deadline");
        CodecStartupGate backwards=preparing();
        check(!backwards.prepared(100,W,H,T+29*MS),"ready cannot precede preparation start");
        check(backwards.phase()==CodecStartupGate.PREPARING,"invalid ready observation preserves pending preparation");
    }
    static void preparationFailures()throws Exception{
        CodecStartupGate configure=preparing();configure.configureFailed();
        check(configure.phase()==CodecStartupGate.FAILED&&configure.failureCode()==CodecStartupGate.CONFIGURE_FAILED,
            "configure failure remains explicit");
        check(!configure.prepared(100,W,H,READY),"failed configure cannot publish ready");
        CodecStartupGate expired=preparing();expired.bootstrapExpired();
        check(expired.phase()==CodecStartupGate.FAILED&&expired.failureCode()==CodecStartupGate.BOOTSTRAP_EXPIRED,
            "expired metadata handoff fails without admitting old AU");
        check(admit(expired,300,READY+MS,true,READY+2*MS)==CodecStartupGate.DROP,"expiry does not silently restart candidate");
        expired.close(READY+3*MS);long[] snapshot=expired.snapshot();
        check(snapshot.length==CodecStartupGate.STAT_NAMES.length,"fixed numeric snapshot contract");
        long[] later=expired.snapshot();snapshot[0]=99;
        check(later[0]==1,"snapshot storage not shared with caller");
    }
    static void synchronizedCancel()throws Exception{
        CodecStartupGate gate=preparing();Object monitor=new Object();
        CountDownLatch closeDone=new CountDownLatch(1);
        AtomicReference<Throwable> failure=new AtomicReference<>();
        Thread completion=new Thread(()->{try{
            closeDone.await();synchronized(monitor){
                check(!gate.prepared(100,W,H,READY),"cancel-before-ready has no late revival");
                check(admit(gate,200,READY+MS,true,READY+2*MS)==CodecStartupGate.DROP,"cancel-before-ready blocks new IDR");
            }
        }catch(Throwable e){failure.set(e);}},"fixture-late-ready");
        completion.start();synchronized(monitor){gate.close(READY-MS);}closeDone.countDown();
        completion.join(2000);check(!completion.isAlive(),"no gate-only blocking wait");
        if(failure.get()!=null)throw new AssertionError("serialized cancellation",failure.get());
        check(gate.phase()==CodecStartupGate.CLOSED,"closed phase published through owner monitor");
    }
    public static void main(String[] args)throws Exception{
        switch(args[0]){
            case "legacy":legacy();break;case "bootstrap":bootstrap();break;
            case "freshness":freshness();break;case "commit_loss":commitLoss();break;
            case "retries":retries();break;case "timeout_close":timeoutClose();break;
            case "preparation_failures":preparationFailures();break;
            case "synchronized_cancel":synchronizedCancel();break;
            default:throw new AssertionError("unknown fixture case");
        }
        System.out.println("PASS "+args[0]+" (actual pure Java gate only)");
    }
}
'''

INBOX_HARNESS = r'''
package local.remoteandroid.direct;

/** Real extracted Inbox + gate; bytes are queue placeholders, never decoded. */
public final class CodecStartupInboxCheck {
    static final long MS=1_000_000L;
    static void check(boolean value,String name){if(!value)throw new AssertionError(name);}
    static UdpVideoProbe.VideoFrame frame(long pts,boolean recovery){
        return new UdpVideoProbe.VideoFrame(new byte[64],1080,1920,recovery?8:0,36,
            pts,System.nanoTime(),recovery,0);
    }
    static UdpVideoProbe.VideoInbox inbox(){return new UdpVideoProbe.VideoInbox(false,null,true,System.nanoTime());}
    static UdpVideoProbe.VideoInbox ready()throws Exception{
        UdpVideoProbe.VideoInbox inbox=inbox();
        check(inbox.offer(frame(100,true)),"bootstrap metadata enters worker handoff");
        UdpVideoProbe.VideoFrame metadata=inbox.take();
        check(metadata.startupPreparation,"metadata tag preserved across epoch wrapper");
        check(inbox.beginStartupPreparation(metadata,System.nanoTime()),"metadata live at worker handoff");
        check(inbox.completeStartupPreparation(metadata,System.nanoTime()),"decoder-ready signal accepted");
        check(inbox.startup.phase()==CodecStartupGate.WAIT_FRESH_IDR,"fresh recovery required");
        return inbox;
    }
    static void metadata()throws Exception{
        UdpVideoProbe.VideoInbox inbox=inbox();
        check(!inbox.offer(frame(1,false)),"startup reference chain rejected");
        check(inbox.offer(frame(100,true)),"bootstrap metadata selected");
        UdpVideoProbe.VideoFrame metadata=inbox.take();
        for(int i=0;i<100;i++)check(!inbox.offer(frame(101+i,false)),"preparing P burst blocked");
        check(inbox.queue.isEmpty()&&inbox.bytes==0&&inbox.overflowEvents==0,
            "preparing burst cannot consume steady FIFO slots");
        check(inbox.beginStartupPreparation(metadata,System.nanoTime()),"bounded handoff starts");
        Thread.sleep(90); // One bounded wall wait: emulate configure exceeding old AU budget.
        check(inbox.completeStartupPreparation(metadata,System.nanoTime()),"metadata may finish after original AU expires");
        check(inbox.needsIdr()&&inbox.queue.isEmpty(),"old AU never staged as media");
        check(inbox.offer(frame(500,true)),"post-ready fresh IDR admitted");
        UdpVideoProbe.VideoFrame fresh=inbox.take();
        check(!fresh.startupPreparation&&fresh.ptsUs==500,"worker receives fresh media instead of bootstrap");
        inbox.success(fresh);
        check(inbox.startup.phase()==CodecStartupGate.STREAMING&&!inbox.needsIdr(),"successful current fresh IDR opens stream");
        UdpVideoProbe.VideoInbox expired=inbox();expired.offer(frame(100,true));
        UdpVideoProbe.VideoFrame late=expired.take();Thread.sleep(90);
        check(!expired.beginStartupPreparation(late,System.nanoTime()),"old metadata cannot begin preparation");
        check(expired.startup.failureCode()==CodecStartupGate.BOOTSTRAP_EXPIRED,"expired handoff fails explicitly");
    }
    static void epochCommit()throws Exception{
        UdpVideoProbe.VideoInbox inbox=ready();inbox.offer(frame(200,true));
        UdpVideoProbe.VideoFrame old=inbox.take();
        for(int i=0;i<4;i++)check(inbox.offer(frame(201+i,false)),"four steady queue slots accepted");
        check(!inbox.offer(frame(205,false)),"fifth reference overflows and breaks chain");
        check(!inbox.valid(old)&&inbox.startup.phase()==CodecStartupGate.WAIT_FRESH_IDR,"epoch loss reaches actual gate");
        inbox.success(old);
        check(inbox.needsIdr()&&inbox.startup.phase()==CodecStartupGate.WAIT_FRESH_IDR,"old epoch IDR completion cannot reopen chain");
        check(inbox.offer(frame(300,true)),"fresh current-epoch recovery selected");
        UdpVideoProbe.VideoFrame fresh=inbox.take();inbox.success(fresh);
        check(!inbox.needsIdr()&&inbox.startup.phase()==CodecStartupGate.STREAMING,"Inbox+gate current commit is atomic");
    }
    static void replacement()throws Exception{
        UdpVideoProbe.VideoInbox inbox=ready();inbox.offer(frame(200,true));
        UdpVideoProbe.VideoFrame old=inbox.take();
        for(int i=0;i<4;i++)check(inbox.offer(frame(201+i,false)),"pending chain fills FIFO");
        check(inbox.offer(frame(300,true)),"overflow replacement complete IDR admitted");
        check(inbox.queue.size()==1&&inbox.needsIdr(),"replacement clears dependent queue");
        UdpVideoProbe.VideoFrame replacement=inbox.take();
        inbox.success(old);
        check(inbox.needsIdr(),"stale completion cannot precede replacement commit");
        inbox.success(replacement);
        check(!inbox.needsIdr()&&inbox.startup.phase()==CodecStartupGate.STREAMING,
            "overflow replacement selects gate identity in new epoch");
    }
    static void cancel()throws Exception{
        UdpVideoProbe.VideoInbox preparing=inbox();preparing.offer(frame(100,true));
        UdpVideoProbe.VideoFrame metadata=preparing.take();
        check(preparing.beginStartupPreparation(metadata,System.nanoTime()),"preparation selected before cancellation");
        preparing.close();
        check(!preparing.completeStartupPreparation(metadata,System.nanoTime()),"cancel prevents late ready publication");
        preparing.success(metadata);
        check(preparing.startup.phase()==CodecStartupGate.CLOSED,"metadata success cannot revive cancelled gate");
        UdpVideoProbe.VideoInbox pending=ready();pending.offer(frame(200,true));
        UdpVideoProbe.VideoFrame fresh=pending.take();pending.close();pending.success(fresh);
        check(pending.startup.phase()==CodecStartupGate.CLOSED,"cancel prevents late media commit gate revival");
        check(!pending.offer(frame(300,true)),"closed Inbox blocks new media admission");
    }
    static void generation()throws Exception{
        UdpVideoProbe.VideoInbox pending=ready();pending.offer(frame(200,true));
        UdpVideoProbe.VideoFrame fresh=pending.take();MainActivity activity=new MainActivity();
        activity.running=true;activity.generation=7;activity.video=new Object();
        pending.success(fresh,activity,6,false);
        check(pending.needsIdr()&&pending.startup.phase()==CodecStartupGate.CHAIN_PENDING,"old generation cannot commit current epoch");
        activity.running=false;pending.success(fresh,activity,7,false);
        check(pending.needsIdr(),"stopped Activity cannot commit");
        activity.running=true;activity.video=null;pending.success(fresh,activity,7,false);
        check(pending.needsIdr(),"configure early return without decoder cannot commit");
        activity.video=new Object();pending.success(fresh,activity,7,true);
        check(pending.needsIdr(),"worker stop cannot commit");
        pending.success(fresh,activity,7,false);
        check(!pending.needsIdr()&&pending.startup.phase()==CodecStartupGate.STREAMING,"live generation commits atomically");
        UdpVideoProbe.VideoInbox cancelled=ready();cancelled.offer(frame(300,true));
        UdpVideoProbe.VideoFrame after=cancelled.take();cancelled.cancelStartup();
        cancelled.success(after,activity,7,false);
        check(cancelled.needsIdr()&&cancelled.startup.phase()==CodecStartupGate.CLOSED,
            "App cancelStartup monitor prevents late commit without requiring FIFO close");
    }
    static void offDrain()throws Exception{
        UdpVideoProbe.VideoInbox off=new UdpVideoProbe.VideoInbox();
        check(off.offer(frame(100,true))&&off.offer(frame(101,false)),"OFF stages original complete IDR and dependent P");
        UdpVideoProbe.VideoFrame idr=off.take();off.close();off.success(idr);
        check(!off.needsIdr(),"OFF close drain still commits original recovery IDR");
        UdpVideoProbe.VideoFrame p=off.take();
        check(p.ptsUs==101&&off.valid(p),"OFF pending reference remains valid during original drain");
        check(off.take()==null,"OFF original drain finishes owned queue");
        UdpVideoProbe.VideoInbox on=ready();on.offer(frame(200,true));
        UdpVideoProbe.VideoFrame fresh=on.take();on.close();on.success(fresh);
        check(on.needsIdr()&&on.startup.phase()==CodecStartupGate.CLOSED,"ON closed startup cannot commit fresh chain");
    }
    public static void main(String[] args)throws Exception{
        switch(args[0]){
            case "metadata":metadata();break;case "epoch_commit":epochCommit();break;
            case "replacement":replacement();break;case "cancel":cancel();break;
            case "generation":generation();break;
            case "off_drain":offDrain();break;
            default:throw new AssertionError("unknown actual Inbox fixture case");
        }
        System.out.println("PASS "+args[0]+" (actual Inbox+pure gate only)");
    }
}
'''


class UdpCodecStartupContractChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.java = str(JDK / 'java') if (JDK / 'java').is_file() else shutil.which('java')
        javac = str(JDK / 'javac') if (JDK / 'javac').is_file() else shutil.which('javac')
        if not cls.java or not javac:
            raise RuntimeError('Existing JDK required for actual Java startup gate fixture')
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-codec-startup-contract-')
        cls.addClassCleanup(cls.folder.cleanup)
        harness = Path(cls.folder.name) / 'CodecStartupContractCheck.java'
        harness.write_text(HARNESS)
        inbox_harness = Path(cls.folder.name) / 'CodecStartupInboxCheck.java'
        inbox_harness.write_text(INBOX_HARNESS)
        probe = (ROOT / 'experiments/nps-transport/phone/UdpVideoProbe.java').read_text()
        # Copy the actual nested types, never a parallel implementation of FIFO,
        # epoch, or startup policy. Android-facing runner code is deliberately out.
        nested = '    static final class InboxEvent' + probe.split(
            '    static final class InboxEvent', 1)[1].split(
            '    // App mode is memory-only;', 1)[0]
        inbox_source = Path(cls.folder.name) / 'UdpVideoProbe.java'
        inbox_source.write_text('package local.remoteandroid.direct;\n'
                               'import java.util.ArrayDeque; import org.json.JSONObject; import org.json.JSONArray;\n'
                               'final class UdpVideoProbe {\n'
                               'static final int MAX_INBOX_EVENTS=256;\n' + nested + '\n}\n')
        activity_source = Path(cls.folder.name) / 'MainActivity.java'
        activity_source.write_text('package local.remoteandroid.direct; final class MainActivity {'
                                   'volatile boolean running; volatile int generation; volatile Object video;}')
        json_dir = Path(cls.folder.name) / 'org/json'
        json_dir.mkdir(parents=True)
        # Fixtures inspect primitive fields, not JSON. These inert signatures
        # satisfy only the unused numeric report methods' compilation dependency.
        json_object = json_dir / 'JSONObject.java'
        json_array = json_dir / 'JSONArray.java'
        json_object.write_text('package org.json; public final class JSONObject {'
                               'public JSONObject put(String key,Object value){return this;}}')
        json_array.write_text('package org.json; public final class JSONArray {'
                              'public JSONArray put(Object value){return this;}}')
        result = subprocess.run([javac, '-d', cls.folder.name, str(SOURCE), str(harness),
                                 str(inbox_harness), str(inbox_source), str(json_object), str(json_array),
                                 str(activity_source),
                                 str(ROOT / 'app/src/main/java/local/remoteandroid/direct/MediaPresentationMetrics.java')],
                                capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Actual Java gate fixture compile failed:\n' + result.stderr)

    def run_case(self, name, inbox=False):
        main = 'CodecStartupInboxCheck' if inbox else 'CodecStartupContractCheck'
        result = subprocess.run([self.java, '-cp', self.folder.name,
                                 'local.remoteandroid.direct.' + main, name],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS ' + name, result.stdout)

    def test_default_is_unchanged_until_explicit_opt_in(self):
        self.run_case('legacy')

    def test_bootstrap_is_metadata_only_and_prepare_bursts_do_not_open_chain(self):
        self.run_case('bootstrap')

    def test_fresh_idr_requires_ready_geometry_pts_and_original_age(self):
        self.run_case('freshness')

    def test_config_without_au_commit_and_stale_commit_do_not_open_chain(self):
        self.run_case('commit_loss')

    def test_keyframe_requests_have_bounded_budget_and_spacing(self):
        self.run_case('retries')

    def test_timeout_cancel_and_reconnect_do_not_reuse_old_state(self):
        self.run_case('timeout_close')

    def test_external_owner_monitor_orders_cancel_before_late_ready(self):
        self.run_case('synchronized_cancel')

    def test_preparation_failure_is_explicit_and_snapshots_are_bounded_values(self):
        self.run_case('preparation_failures')

    def test_actual_inbox_stages_metadata_without_opening_reference_fifo(self):
        self.run_case('metadata', inbox=True)

    def test_actual_inbox_epoch_loss_and_success_update_gate_atomically(self):
        self.run_case('epoch_commit', inbox=True)

    def test_actual_inbox_overflow_idr_replacement_selects_new_epoch_identity(self):
        self.run_case('replacement', inbox=True)

    def test_actual_inbox_cancel_blocks_ready_and_gate_revival(self):
        self.run_case('cancel', inbox=True)

    def test_actual_inbox_commit_requires_live_generation_and_app_cancel_monitor(self):
        self.run_case('generation', inbox=True)

    def test_actual_inbox_off_close_drain_retains_original_reference_recovery(self):
        self.run_case('off_drain', inbox=True)


if __name__ == '__main__':
    unittest.main()
