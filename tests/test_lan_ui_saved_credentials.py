"""Offline owner helper fixtures; no device, credential store or media started."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('saved_ui_driver', ROOT/'scripts/probes/run_authenticated_lan_ui.py')
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)
FIXTURE_SPEC = importlib.util.spec_from_file_location('saved_ui_complete_fixture', ROOT/'tests/test_nps_ui_driver.py')
FIXTURE = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(FIXTURE)
HELPER = ROOT/'experiments/moonlight-v2/authenticated-lan/LanUiAcceptance.java'
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')


def saved_report(node='m1'):
    report = FIXTURE.complete_report(node)
    report.update(requested_credential_source='saved-ui', saved_UI_private_input_touched=False,
                  saved_UI_secret_exported=False, helper_owned_attempt_started=True)
    for stage in ('first', 'second'):
        report.update({stage+'_'+field:True for field in ('saved_UI_route_verified',
            'saved_UI_allowed_account_verified', 'saved_UI_nonempty_credential_verified',
            'saved_UI_normal_restore_used')})
    return report


class CompletedHelper:
    returncode = 0
    def __init__(self, report): self.report = report
    def poll(self): return 0
    def communicate(self, timeout):
        return 'INSTRUMENTATION_RESULT: numeric_result='+json.dumps(self.report), ''


class SavedUiDriverChecks(unittest.TestCase):
    def invoke(self, folder, *, source='saved-ui', pid='absent', helper=None):
        calls, spawned = [], []
        def run(command, **kwargs):
            calls.append(command)
            if command[0] == 'lsof':
                return subprocess.CompletedProcess(command, 1, stdout=b'', stderr=b'')
            if command[-1] == 'pidof '+DRIVER.TARGET_PACKAGE:
                if pid == 'absent': return subprocess.CompletedProcess(command, 1, stdout='', stderr='')
                if pid == 'active': return subprocess.CompletedProcess(command, 0, stdout='9876', stderr='')
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='private transport detail')
            return subprocess.CompletedProcess(command, 0, stdout='{}' if 'cat ' in command[-1] else '', stderr='')
        def spawn(command, **kwargs):
            spawned.append(command)
            return CompletedHelper(helper or saved_report())
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(DRIVER.sys, 'argv', ['probe', '--output', str(folder),
                '--network-scope', 'nps_owner', '--node', 'm1', '--media-only',
                '--credential-source', source, '--stage-diagnostics', 'off']))
            stack.enter_context(patch.object(DRIVER.subprocess, 'run', side_effect=run))
            stack.enter_context(patch.object(DRIVER.subprocess, 'Popen', side_effect=spawn))
            stack.enter_context(patch.object(DRIVER.time, 'monotonic', return_value=0))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            result = DRIVER.main()
        return result, json.loads((folder/'ui-acceptance.json').read_text()), calls, spawned

    def test_saved_mode_is_explicit_exact_public_read_and_private_file_stays_default(self):
        common = ['--output', '/fake/evidence']
        self.assertEqual(DRIVER.parse_arguments(common).credential_source, 'private-file')
        saved = DRIVER.parse_arguments(common+['--network-scope', 'nps_owner', '--node', 'm1', '--credential-source', 'saved-ui'])
        self.assertEqual(saved.credential_source, 'saved-ui')
        for bad in (['--credential-source', 'saved-ui'],
                    ['--network-scope', 'tailnet', '--credential-source', 'saved-ui'],
                    ['--network-scope', 'nps_owner', '--node', 'm1', '--credential-source', 'saved-ui', '--credential-save', 'on']):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit): DRIVER.parse_arguments(common+bad)

    def test_success_forwards_only_mode_and_has_no_private_input_touch_or_password_argument(self):
        report = saved_report()
        report['requested_stage_diagnostics_enabled'] = False
        for stage in ('first', 'second'): report[stage+'_stage_diagnostics_enabled'] = 0
        with tempfile.TemporaryDirectory() as folder:
            code, actual, calls, spawned = self.invoke(Path(folder), helper=report)
        self.assertEqual(code, 0)
        self.assertTrue(actual['preinstrument_target_process_absent_verified'])
        self.assertFalse(actual['instrumentation_no_restart_lifecycle_verified'])
        self.assertTrue(actual['credential_source_readback_verified'])
        self.assertIn('-e credential_source saved-ui', spawned[0][-1])
        self.assertIn('-e network_scope nps_owner -e node m1', spawned[0][-1])
        self.assertFalse(any('udp-test-login.json' in command[-1] for command in calls))
        self.assertFalse(any('force-stop' in command[-1] for command in calls))
        self.assertNotIn('--no-restart', spawned[0][-1])
        self.assertNotIn('-e password', spawned[0][-1])
        self.assertNotIn('-e username', spawned[0][-1])

    def test_both_modes_skip_running_or_uncertain_target_before_instrumentation(self):
        for source in ('private-file', 'saved-ui'):
            for pid in ('active', 'unavailable'):
                with self.subTest(source=source, pid=pid), tempfile.TemporaryDirectory() as folder:
                    code, report, calls, spawned = self.invoke(Path(folder), source=source, pid=pid)
                self.assertEqual((code, spawned), (1, []))
                self.assertFalse(report['preinstrument_target_process_absent_verified'])
                self.assertFalse(any('force-stop' in command[-1] for command in calls))
                self.assertNotIn('private transport detail', json.dumps(report))
                if source == 'saved-ui':
                    self.assertEqual(len(calls), 2)  # host gate + PID check; no App file cleanup owned.
                    self.assertFalse(any('udp-test-login.json' in command[-1] for command in calls))

    def test_missing_or_wrong_saved_entry_never_force_stops_or_falls_back_to_private_input(self):
        for label in ('saved_UI_credential_unavailable', 'saved_UI_account_unavailable',
                      'saved_UI_route_unverified', 'saved_UI_restore_unavailable'):
            for owned_started in (False, True):  # Also protect a missing entry at reconnect.
                helper = {'requested_credential_source':'saved-ui', 'requested_credential_save_acceptance':False,
                    'helper_owned_attempt_started':owned_started, 'failure_class':'IllegalStateException',
                    'bounded_failure_label':label}
                with self.subTest(label=label, reconnect=owned_started), tempfile.TemporaryDirectory() as folder:
                    code, report, calls, spawned = self.invoke(Path(folder), helper=helper)
                self.assertEqual((code, len(spawned)), (1, 1))
                self.assertEqual(report['driver_failure_label'], label)
                self.assertTrue(report['saved_UI_unavailable_skip'])
                self.assertIs(report['saved_UI_declined_before_owned_attempt'], not owned_started)
                self.assertFalse(any('force-stop' in command[-1] for command in calls))
                self.assertFalse(any('udp-test-login.json' in command[-1] for command in calls))

    def test_helper_busy_at_first_or_reconnect_never_force_stops_an_unowned_app(self):
        for media_started in (False, True):
            helper = {'failure_class':'IllegalStateException', 'bounded_failure_label':'existing_UI_attempt_busy',
                      'normal_UI_login_received_media':media_started}
            with tempfile.TemporaryDirectory() as folder:
                code, report, calls, _ = self.invoke(Path(folder), helper=helper)
            self.assertEqual(code, 1)
            self.assertTrue(report['existing_phone_UI_attempt_busy_skip'])
            self.assertFalse(any('force-stop' in command[-1] for command in calls))

    def test_saved_source_verifier_requires_both_stages_and_strict_boolean_boundaries(self):
        valid = saved_report()
        self.assertTrue(DRIVER.verify_credential_source_readback(valid, 'saved-ui'))
        self.assertFalse(DRIVER.verify_credential_source_readback(valid, 'private-file'))
        for key in ('saved_UI_private_input_touched', 'saved_UI_secret_exported',
                    *[stage+'_'+field for stage in ('first', 'second') for field in (
                        'saved_UI_route_verified', 'saved_UI_allowed_account_verified',
                        'saved_UI_nonempty_credential_verified', 'saved_UI_normal_restore_used')]):
            changed = copy.deepcopy(valid); del changed[key]
            self.assertFalse(DRIVER.verify_credential_source_readback(changed, 'saved-ui'), key)
            changed = copy.deepcopy(valid); changed[key] = 1 if valid[key] else 0
            self.assertFalse(DRIVER.verify_credential_source_readback(changed, 'saved-ui'), key)
        self.assertTrue(DRIVER.verify_credential_source_readback({}, 'private-file'))

    def test_pidof_absence_is_not_inferred_from_a_failed_or_malformed_query(self):
        def state(code, stdout='', stderr=''):
            return DRIVER.target_process_state(subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr))
        self.assertEqual(state(1), 'absent')
        self.assertEqual(state(0, '123 234'), 'active')
        for case in ((0, '', ''), (1, '', 'offline'), (2, '', ''),
                     (0, '0', ''), (0, '99999999999999999', ''),
                     (0, 'secret', ''), (False, '123', ''), (1, b'', b'')):
            self.assertEqual(state(*case), 'unavailable', case)


def actual_method(text, marker):
    start=text.index(marker); opening=text.index('{',start); depth=1; end=opening+1
    while depth:
        depth += (text[end]=='{')-(text[end]=='}'); end+=1
    return text[start:end]


class SavedUiActualJavaChecks(unittest.TestCase):
    """Execute the actual helper validator/sequence with inert UI/clock doubles.

    Not Android instrumentation lifecycle, Keystore or real posted UI acceptance.
    Password editable deliberately throws if converted to a String.
    """
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-saved-ui-fixture-')
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        original = HELPER.read_text()
        markers = ('    private static boolean authorizedTrialAccount(',
            '    private static String savedUiValidationFailure(', '    private void assertIdleUi(',
            '    private String savedUiValidation(', '    private void throwSavedUiFailure(',
            '    private void prepareSavedUi(', '    private static Object field(',
            '    private static boolean awaitUiCallback(')
        methods = '\n'.join(actual_method(original, marker) for marker in markers)
        source = r'''
import java.lang.reflect.Field;import java.util.*;
public final class SavedUiSequenceCheck{
 static long now;static Runnable queued;static int mediaOptions;static final String address="146.56.249.175:49556";
 static final class SystemClock{static long elapsedRealtime(){now+=20;if(queued!=null&&now>=60){Runnable task=queued;queued=null;task.run();}return now;}}
 interface CallbackPause{void sleep(long millis)throws InterruptedException;}
 static final class Text{String value;int secretLength;final boolean secret;Text(String value){this.value=value;secret=false;}Text(int length){secretLength=length;secret=true;}int length(){return secret?secretLength:value.length();}public String toString(){if(secret)throw new AssertionError("secret converted to String");return value;}}
 static final class EditText{Text text;EditText(Text text){this.text=text;}Text getText(){return text;}void setText(String value){throw new AssertionError("saved UI field written before its normal restore");}}
 static final class Spinner{int selection=3;Ui ui;int getSelectedItemPosition(){return selection;}void setSelection(int index){selection=index;queued=()->{ui.address.text.value=address;ui.user.text.value=ui.savedUser;ui.password.text.secretLength=ui.savedLength;ui.restoringFields=false;};ui.restoringFields=true;}}
 static final class Ui{final Object lock=new Object();Object current,retiring;boolean restoringFields;final Spinner scope=new Spinner();final EditText address=new EditText(new Text("146.56.249.175:49558")),user=new EditText(new Text("huoguo")),password=new EditText(new Text(0));String savedUser="huoguo";int savedLength=8;Ui(){scope.ui=this;}}
 static final class MainActivity{final Ui lanUdpEntry=new Ui();}
 static final class JSONObject{final Map<String,Object> values=new HashMap<>();JSONObject put(String key,Object value){if(!(value instanceof Boolean))throw new AssertionError("nonboolean report");values.put(key,value);return this;}}
 void runOnMainSync(Runnable task){task.run();}void waitForIdleSync(){}int scopeIndex(){return 2;}String controlAddress(){return address;}void applyMediaOptions(MainActivity target,Object ui){mediaOptions++;}
 METHODS
 static void ok(boolean value){if(!value)throw new AssertionError("check failed");}
 public static void main(String[] args)throws Exception{
  String mode=args[0];SavedUiSequenceCheck test=new SavedUiSequenceCheck();MainActivity target=new MainActivity();JSONObject report=new JSONObject();
  if(mode.equals("bounds")){ok(savedUiValidationFailure(2,2,address,address,"huoguo",1).isEmpty());ok(savedUiValidationFailure(2,2,address,address,"wyw",1024).isEmpty());ok(savedUiValidationFailure(3,2,address,address,"huoguo",8).equals("saved_UI_route_unverified"));ok(savedUiValidationFailure(2,2,address+"/wrong",address,"huoguo",8).equals("saved_UI_route_unverified"));ok(savedUiValidationFailure(2,2,address,address,"another",8).equals("saved_UI_account_unavailable"));for(int length:new int[]{0,1025,-1})ok(savedUiValidationFailure(2,2,address,address,"huoguo",length).equals("saved_UI_credential_unavailable"));}
  else if(mode.equals("restore")){test.prepareSavedUi(target,report,"first");ok(now>=60&&queued==null&&mediaOptions==1&&report.values.size()==4);target.lanUdpEntry.password.text.secretLength=0;test.prepareSavedUi(target,report,"second");ok(mediaOptions==2&&report.values.size()==8);}
  else if(mode.equals("missing")||mode.equals("account")){if(mode.equals("missing"))target.lanUdpEntry.savedLength=0;else target.lanUdpEntry.savedUser="another";try{test.prepareSavedUi(target,report,"first");throw new AssertionError("unavailable accepted");}catch(IllegalStateException expected){ok(expected.getMessage().equals(mode.equals("missing")?"saved_UI_credential_unavailable":"saved_UI_account_unavailable"));}ok(mediaOptions==0&&report.values.isEmpty()&&now<=1600);}
  else if(mode.equals("busy")){Object existing=new Object();target.lanUdpEntry.current=existing;try{test.prepareSavedUi(target,report,"first");throw new AssertionError("busy accepted");}catch(IllegalStateException expected){ok(expected.getMessage().equals("existing_UI_attempt_busy"));}ok(target.lanUdpEntry.current==existing&&target.lanUdpEntry.scope.selection==3&&queued==null&&report.values.isEmpty());}
  else throw new AssertionError("mode");System.out.println("actual saved-ui fixture passed");
 }
}
'''.replace(' METHODS', methods)
        path = Path(cls.folder.name)/'SavedUiSequenceCheck.java'; path.write_text(source)
        result = subprocess.run([javac, '-d', cls.folder.name, str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode:
            cls.folder.cleanup(); raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls): cls.folder.cleanup()

    def run_case(self, mode):
        result = subprocess.run([self.java, '-cp', self.folder.name, 'SavedUiSequenceCheck', mode],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('actual saved-ui fixture passed', result.stdout)

    def test_actual_validation_enforces_endpoint_account_and_secret_length_bounds(self): self.run_case('bounds')
    def test_actual_prepare_waits_posted_restore_and_repeats_it_at_reconnect(self): self.run_case('restore')
    def test_missing_saved_entry_is_bounded_and_does_not_convert_or_rewrite_secret(self): self.run_case('missing')
    def test_unlisted_saved_account_is_bounded_unavailable(self): self.run_case('account')
    def test_busy_preparation_preserves_existing_attempt_and_route(self): self.run_case('busy')


if __name__ == '__main__': unittest.main()
