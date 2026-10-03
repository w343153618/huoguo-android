"""Execute the actual beta login-header method with inert widget doubles.

This checks layout requests and click ownership, not Android measurement,
installation or real V50 visual acceptance.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
JDK = Path('/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home/bin')
UI = ROOT/'app/src/udp/java/local/remoteandroid/direct/AuthenticatedLanUdpUi.java'


class UdpLoginUpdateChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='huoguo-login-update-')
        cls.java = str(JDK/'java') if (JDK/'java').is_file() else shutil.which('java')
        javac = str(JDK/'javac') if (JDK/'javac').is_file() else shutil.which('javac')
        source = UI.read_text()
        method = '    private LinearLayout loginHeader' + source.split('    private LinearLayout loginHeader', 1)[1].split('    private EditText field', 1)[0]
        harness = r'''package local.remoteandroid.direct;
import java.util.*;
public final class LoginUpdateCheck {
    private final MainActivity activity=new MainActivity();
    private long loginRevision=7;private boolean live;
    private boolean active(){return live;}
    static final class Gravity {static final int CENTER_VERTICAL=16;}
    static class View {LayoutParams params;}
    static class LayoutParams {final int width,height;final float weight;
        LayoutParams(int w,int h,float n){width=w;height=h;weight=n;}}
    static final class LinearLayout extends View {
        static final int HORIZONTAL=0;int orientation=-1,gravity=-1;
        final List<View> children=new ArrayList<>();LinearLayout(MainActivity a){}
        void setOrientation(int n){orientation=n;}void setGravity(int n){gravity=n;}
        void addView(View v,LayoutParams p){v.params=p;children.add(v);}
        static final class LayoutParams extends LoginUpdateCheck.LayoutParams {
            LayoutParams(int w,int h){super(w,h,0);}LayoutParams(int w,int h,float n){super(w,h,n);}}
    }
    static class TextView extends View {String text;float size;TextView(MainActivity a){}
        void setText(String s){text=s;}void setTextSize(float n){size=n;}}
    static final class Button extends TextView {
        interface Listener{void click(View v);}Listener listener;boolean caps;
        int minWidth=-1,minimumWidth=-1,minHeight=-1,minimumHeight=-1,left,right,top,bottom;
        Button(MainActivity a){super(a);}void setAllCaps(boolean value){caps=value;}
        void setMinWidth(int n){minWidth=n;}void setMinimumWidth(int n){minimumWidth=n;}
        void setMinHeight(int n){minHeight=n;}void setMinimumHeight(int n){minimumHeight=n;}
        void setPadding(int l,int t,int r,int b){left=l;top=t;right=r;bottom=b;}
        void setOnClickListener(Listener l){listener=l;}void performClick(){if(listener==null)throw new AssertionError("unwired");listener.click(this);}
    }
    static final class Metrics {float density=1;}
    static final class Resources {final Metrics metrics=new Metrics();Metrics getDisplayMetrics(){return metrics;}}
    static final class Updater {int checks;boolean manual;void check(boolean value){checks++;manual=value;}}
    static final class MainActivity {boolean finishing,destroyed;final Resources resources=new Resources();final Updater updater=new Updater();
        boolean isFinishing(){return finishing;}boolean isDestroyed(){return destroyed;}Resources getResources(){return resources;}}
''' + method + r'''
    static void ok(boolean value){if(!value)throw new AssertionError();}
    Button action(LinearLayout row){return (Button)row.children.get(1);}
    public static void main(String[] args){
        LoginUpdateCheck test=new LoginUpdateCheck();String mode=args[0];
        if(mode.equals("layout")){
            for(float density:new float[]{1f,2f,3.5f}){
                test.activity.resources.metrics.density=density;LinearLayout row=test.loginHeader(7);Button update=test.action(row);TextView title=(TextView)row.children.get(0);
                ok(row.orientation==LinearLayout.HORIZONTAL&&row.gravity==Gravity.CENTER_VERTICAL&&row.children.size()==2);
                ok(title.params.width==0&&title.params.height==-2&&title.params.weight==1&&title.text.equals("给火锅的安卓 · 测试版"));
                ok(update.params.width==-2&&update.params.height==-2&&update.params.weight==0&&update.text.equals("检查更新"));
                ok(update.minWidth==0&&update.minimumWidth==0&&update.minHeight==Math.round(48*density)&&update.minimumHeight==Math.round(48*density));
                ok(update.left==Math.round(12*density)&&update.right==update.left&&update.top==0&&update.bottom==0&&!update.caps);
            }
        }else{
            Button update=test.action(test.loginHeader(7));
            if(mode.equals("live"))test.live=true;
            else if(mode.equals("stale"))test.loginRevision=8;
            else if(mode.equals("finishing"))test.activity.finishing=true;
            else if(mode.equals("destroyed"))test.activity.destroyed=true;
            else if(!mode.equals("click"))throw new AssertionError("mode");
            update.performClick();ok(test.activity.updater.checks==(mode.equals("click")?1:0));
            if(mode.equals("click"))ok(test.activity.updater.manual);
        }
        System.out.println("actual beta header checks passed");
    }
}
'''
        target = Path(cls.folder.name)/'LoginUpdateCheck.java'
        target.write_text(harness)
        result = subprocess.run([javac, '-d', cls.folder.name, target], capture_output=True, text=True, timeout=30)
        if result.returncode:
            cls.folder.cleanup()
            raise AssertionError(result.stdout+result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def run_case(self, mode):
        result = subprocess.run([self.java, '-cp', self.folder.name,
            'local.remoteandroid.direct.LoginUpdateCheck', mode], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('actual beta header checks passed', result.stdout)

    def test_weighted_title_and_compact_right_action_keep_48dp_touch_request(self):self.run_case('layout')
    def test_actual_button_listener_reuses_manual_updater_channel_flow(self):self.run_case('click')
    def test_retained_button_cannot_update_during_media(self):self.run_case('live')
    def test_retained_old_login_button_cannot_update_on_new_page(self):self.run_case('stale')
    def test_finishing_activity_cannot_launch_update_dialog(self):self.run_case('finishing')
    def test_destroyed_activity_cannot_launch_update_dialog(self):self.run_case('destroyed')

    def test_header_remains_inside_existing_login_system_bar_insets(self):
        source = UI.read_text()
        login = source.split('@Override public void showLogin()', 1)[1].split('private LinearLayout loginHeader', 1)[0]
        self.assertIn('box.addView(loginHeader(pageRevision))', login)
        self.assertNotIn('Button update=', login)
        self.assertIn('scroll.addView(box)', login)
        self.assertIn('WindowInsets.Type.systemBars()', login)
        self.assertIn('view.setPadding(bars.left,bars.top,bars.right,bars.bottom)', login)


if __name__ == '__main__':
    unittest.main()
