package local.remoteandroid.direct;
import android.app.*;import android.os.*;import android.media.*;import android.view.*;import android.widget.*;import android.text.InputType;import android.util.Base64;import org.json.JSONObject;import java.io.*;import java.nio.*;import java.nio.charset.StandardCharsets;import java.security.*;import java.security.cert.*;import java.util.*;import javax.net.ssl.*;
/** Native scrcpy 4.1 client. The only input endpoint is Android's control socket. */
public class MainActivity extends Activity {
 LanUdpEntry lanUdpEntry;
 static final String PUBLIC_HOST=ConnectionRoutes.PUBLIC_HOST,TAILSCALE_HOST=ConnectionRoutes.TAILSCALE_HOST;
 final java.util.concurrent.atomic.AtomicLong receivedVideoBytes=new java.util.concurrent.atomic.AtomicLong(),receivedFrames=new java.util.concurrent.atomic.AtomicLong(),presentedFrames=new java.util.concurrent.atomic.AtomicLong(),lateDiscardedFrames=new java.util.concurrent.atomic.AtomicLong(),audioOutputBytes=new java.util.concurrent.atomic.AtomicLong();
 final Handler ui=new Handler(Looper.getMainLooper());final HandlerThread statsThread=new HandlerThread("render-stats");long lastMoveMs;volatile boolean hardwareVideo;volatile long networkRttMs=-1;volatile PlaybackClock playback;volatile float audioGain=1f;TextView perf;
 final List<SSLSocket> sockets=Collections.synchronizedList(new ArrayList<>());volatile boolean running;volatile int width=720,height=1280,generation;volatile MediaCodec video,audio;volatile DataOutputStream control;SurfaceView screen;FrameLayout canvas;AudioTrack track;SSLContext tls;final List<byte[]> fingerprints=new ArrayList<>();String host,auth;TextView status;EditText address,user,password;final InputQueue input=new InputQueue();final Object controlWriteLock=new Object();Button rotateButton;
 public void onCreate(Bundle b){super.onCreate(b);if(Build.VERSION.SDK_INT>=33)getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,this::handleBack);statsThread.start();updater=new AppUpdater(this);if(Build.VERSION.SDK_INT>=37&&checkSelfPermission("android.permission.ACCESS_LOCAL_NETWORK")!=android.content.pm.PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{"android.permission.ACCESS_LOCAL_NETWORK"},8);getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);setVolumeControlStream(AudioManager.STREAM_MUSIC);if(componentProbeRequested())setContentView(new FrameLayout(this));else login();getWindow().getDecorView().post(()->{WindowInsetsController c=getWindow().getDecorView().getWindowInsetsController();if(c!=null)c.setSystemBarsAppearance(WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS|WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS|WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);});}
 AppUpdater updater;
 MediaTransfer transfer;
 volatile DiagnosticRunner diagnostics;
 volatile MediaPresentationMetrics presentationMetrics;
 // Instrumentation-only A/B controls; never loaded from or written to user preferences.
 volatile boolean probeImmediateVideoRelease;
 volatile int probeAudioBufferFrames;
 volatile int probeVideoSubmissionLeadMs=0; // 0: existing immediate Surface submission, never a saved preference.
 final java.util.concurrent.atomic.AtomicLong probeVideoSubmissionWaits=new java.util.concurrent.atomic.AtomicLong(),probeVideoSubmissionWaitNs=new java.util.concurrent.atomic.AtomicLong(),probeVideoSubmissionWaitedOutputs=new java.util.concurrent.atomic.AtomicLong(),probeVideoSubmissionMaxHoldNs=new java.util.concurrent.atomic.AtomicLong(),probeVideoSubmissionMaxParkNs=new java.util.concurrent.atomic.AtomicLong(),probeVideoSubmissionBudgetFallbacks=new java.util.concurrent.atomic.AtomicLong();
 volatile String videoDecoderName="";
 PasswordStore passwordStore;
 int maxSize=960,bitRate=4000000;int maxFps=30,bufferMs=80;String bitrateMode="VBR";volatile AdaptiveBitrate adaptive;volatile int acceptedBitrate;volatile boolean adaptiveRejected;boolean metricsVisible;volatile boolean soundEnabled=true;int avSyncOffsetMs=0;
 void login(){
  if(BuildConfig.AUTHENTICATED_LAN_UDP&&!componentProbeRequested()){
   try{if(lanUdpEntry==null)lanUdpEntry=(LanUdpEntry)Class.forName("local.remoteandroid.direct.AuthenticatedLanUdpUi").getConstructor(MainActivity.class).newInstance(this);lanUdpEntry.showLogin();}
   catch(Exception failure){status=new TextView(this);status.setText("隔离 UDP 候选构建无法启动："+failure.getClass().getSimpleName());setContentView(status);}return;
  }
  if(componentProbeRequested()){
   status=new TextView(this);setContentView(status);return;
  }
  android.content.SharedPreferences saved=getSharedPreferences("connection",0);
  if(!saved.getBoolean("dual_profile_init_v3",false)){
   DeviceProfiles.Profile def=DeviceProfiles.detectDefaultProfile();
   android.content.SharedPreferences.Editor defaults=saved.edit();
   if(!saved.contains("selected_profile"))defaults.putString("selected_profile",def.id);
   if(!saved.contains("quality_max_size"))defaults.putInt("quality_max_size",saved.contains("quality")?StreamQuality.legacySize(saved.getInt("quality",1)):def.maxSize);
   if(!saved.contains("video_bit_rate"))defaults.putInt("video_bit_rate",def.bitrate);
   if(!saved.contains("bitrate_mode"))defaults.putString("bitrate_mode",def.mode);
   if(!saved.contains("max_fps"))defaults.putInt("max_fps",def.fps);
   if(!saved.contains("buffer_ms"))defaults.putInt("buffer_ms",def.bufferMs);
   if(!saved.contains("audio_gain"))defaults.putFloat("audio_gain",def.audioGain);
   if(!saved.contains("av_sync_offset_ms"))defaults.putInt("av_sync_offset_ms",0);
   if(!saved.contains("metrics_visible"))defaults.putBoolean("metrics_visible",def.showMetrics);
   defaults.putBoolean("dual_profile_init_v3",true);
   defaults.apply();
  }
  if(!saved.getBoolean("nps_defaults_v1",false)){android.content.SharedPreferences.Editor edit=saved.edit();String previous=saved.getString("host","");if(previous.isEmpty())edit.putString("host","146.56.249.175:15556");String previousUser=saved.getString("username","");if(previousUser.isEmpty())edit.putString("username","huoguo");edit.putBoolean("nps_defaults_v1",true).apply();}
  String previousShortcut=saved.getString("host","");String currentShortcut=ConnectionRoutes.migratedShortcut(previousShortcut);if(!previousShortcut.equals(currentShortcut))saved.edit().putString("host",currentShortcut).apply();
  passwordStore=new PasswordStore(this);DeviceProfiles.Profile detected=DeviceProfiles.detectDefaultProfile();maxFps=saved.getInt("max_fps",detected.fps);bufferMs=Math.min(100,Math.max(30,saved.getInt("buffer_ms",detected.bufferMs)));audioGain=saved.getFloat("audio_gain",detected.audioGain);avSyncOffsetMs=saved.getInt("av_sync_offset_ms",detected.avSyncOffsetMs);soundEnabled=saved.getBoolean("sound_enabled",true);
  LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setPadding(CuteUi.dp(this,20),CuteUi.dp(this,16),CuteUi.dp(this,20),CuteUi.dp(this,16));box.setBackgroundColor(0xfff5fbf9);
  TextView title=new TextView(this);title.setText(getString(R.string.app_name)+"\n你的随身小安卓");title.setTextSize(21);title.setTextColor(CuteUi.INK);LinearLayout heading=new LinearLayout(this);heading.setGravity(Gravity.CENTER_VERTICAL);ImageView avatar=CuteUi.avatar(this);LinearLayout.LayoutParams avatarLayout=new LinearLayout.LayoutParams(CuteUi.dp(this,56),CuteUi.dp(this,56));avatarLayout.setMarginEnd(CuteUi.dp(this,10));heading.addView(avatar,avatarLayout);heading.addView(title,new LinearLayout.LayoutParams(0,-2,1));Button update=new Button(this);update.setText("检查更新");CuteUi.style(update,CuteUi.MINT,true);update.setOnClickListener(v->updater.check(true));heading.addView(update,new LinearLayout.LayoutParams(-2,CuteUi.dp(this,48)));box.addView(heading);TextView installed=new TextView(this);installed.setText("已安装版本 v"+BuildConfig.VERSION_NAME+" · 版本码 "+BuildConfig.VERSION_CODE);installed.setTextSize(12);box.addView(installed);
  address=field(box,"服务器 IP 或主机名（可带端口）",saved.getString("host","146.56.249.175:15556"));address.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);
  LinearLayout routes=new LinearLayout(this);Button publicRoute=new Button(this);publicRoute.setText("公网 M1 · 默认");CuteUi.style(publicRoute,CuteUi.MINT,true);routes.addView(publicRoute,new LinearLayout.LayoutParams(0,CuteUi.dp(this,48),1));Button m5Route=new Button(this);m5Route.setText("公网 M5 · 备选");CuteUi.style(m5Route,CuteUi.PINK,true);routes.addView(m5Route,new LinearLayout.LayoutParams(0,CuteUi.dp(this,48),1));Button privateRoute=new Button(this);privateRoute.setText("Tailscale M1 · yilufa");CuteUi.style(privateRoute,CuteUi.PINK,true);routes.addView(privateRoute,new LinearLayout.LayoutParams(0,CuteUi.dp(this,48),1));box.addView(routes);
  publicRoute.setOnClickListener(v->{address.setText(ConnectionRoutes.M1_ENDPOINT);address.setSelection(address.length());});m5Route.setOnClickListener(v->{address.setText(ConnectionRoutes.M5_ENDPOINT);address.setSelection(address.length());});privateRoute.setOnClickListener(v->{address.setText(ConnectionRoutes.TAILSCALE_ENDPOINT);address.setSelection(address.length());});
  TextView routeHint=new TextView(this);routeHint.setText("M1 是默认入口，M5 是备用入口；点选切换或自行填写 IP:端口。Tailscale M1 使用 yilufa 尾网，需先加入同一尾网，可能直连或经中继；当前 App 视频仍使用加密 TCP。");routeHint.setTextSize(12);routeHint.setTextColor(CuteUi.INK);box.addView(routeHint);
  user=field(box,"直连用户名",saved.getString("username","huoguo"));password=field(box,"直连密码（可在本机加密保存）","");password.setInputType(129);
  CheckBox reveal=new CheckBox(this);reveal.setText("显示密码，核对特殊符号");reveal.setOnCheckedChangeListener((button,checked)->{password.setTransformationMethod(checked?android.text.method.HideReturnsTransformationMethod.getInstance():android.text.method.PasswordTransformationMethod.getInstance());password.setSelection(password.length());});box.addView(reveal);
  LinearLayout passwordActions=new LinearLayout(this);Button remember=new Button(this);remember.setText("保存密码");CuteUi.style(remember,CuteUi.MINT,true);passwordActions.addView(remember,new LinearLayout.LayoutParams(0,CuteUi.dp(this,48),1));Button forget=new Button(this);forget.setText("清除已保存密码");CuteUi.style(forget,CuteUi.PINK,true);passwordActions.addView(forget,new LinearLayout.LayoutParams(0,CuteUi.dp(this,48),1));box.addView(passwordActions);
  remember.setOnClickListener(v->{try{String destination=Endpoint.destination(address.getText().toString()),name=user.getText().toString(),secret=password.getText().toString();if(name.isEmpty()||secret.isEmpty())throw new IllegalArgumentException("请先填写用户名和密码");passwordStore.save(Endpoint.identity(destination),name,secret);saved.edit().putString("host",destination).putString("username",name).apply();status.setText("密码已在本机加密保存，下次自动填入。");}catch(Exception e){status.setText("保存失败："+message(e));}});
  forget.setOnClickListener(v->{try{passwordStore.clear();password.setText("");status.setText("已清除保存的密码。");}catch(Exception e){status.setText("清除失败："+message(e));}});
  android.text.TextWatcher accountChanged=new android.text.TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){}public void onTextChanged(CharSequence s,int start,int before,int count){restorePassword();}public void afterTextChanged(android.text.Editable s){}};address.addTextChangedListener(accountChanged);user.addTextChangedListener(accountChanged);
  TextView qualityLabel=new TextView(this);qualityLabel.setText("清晰度（下次连接生效）");box.addView(qualityLabel);
  int remembered=saved.getInt("quality_max_size",saved.contains("quality")?StreamQuality.legacySize(saved.getInt("quality",1)):detected.maxSize);StreamQuality qualityOptions=new StreamQuality(remembered);Spinner quality=new Spinner(this);String[] labels=qualityOptions.labels;int[] sizes=qualityOptions.sizes;
  ArrayAdapter<String> choices=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,labels);choices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);quality.setAdapter(choices);
  maxSize=sizes[qualityOptions.selected];quality.setSelection(qualityOptions.selected);box.addView(quality);quality.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){maxSize=sizes[position];saved.edit().putInt("quality_max_size",maxSize).apply();}public void onNothingSelected(AdapterView<?> parent){}});
  TextView qualityHint=new TextView(this);qualityHint.setText("按远端画面比例缩放，上面的尺寸以 16:9 为例。升级后保留历史清晰度；主动点选标准档位才会更改。");qualityHint.setTextSize(12);box.addView(qualityHint);
  TextView modeLabel=new TextView(this);modeLabel.setText("编码模式（下次连接生效）");box.addView(modeLabel);Spinner mode=new Spinner(this);String[] modes={"CBR · 固定码率目标","VBR · 可变码率","网络自适应 VBR · 实验"};ArrayAdapter<String> modeChoices=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,modes);modeChoices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);mode.setAdapter(modeChoices);mode.setSelection(modeIndex(saved.getString("bitrate_mode","ADAPTIVE_VBR")));box.addView(mode);mode.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){bitrateMode=modeName(position);saved.edit().putString("bitrate_mode",bitrateMode).apply();}public void onNothingSelected(AdapterView<?> parent){}});TextView modeHint=new TextView(this);modeHint.setText("此前你的电信移动网络测试偏好 VBR；后续重复测试仍有波动。网络自适应 VBR 按传输延迟调整目标，填写的码率作为目标上限；它不是编码器原生 AVBR，也不是严格峰值限流。下次连接生效。");modeHint.setTextSize(12);box.addView(modeHint);
  TextView rateLabel=new TextView(this);rateLabel.setText("目标码率（Mbps，下次连接生效）");box.addView(rateLabel);
  Spinner rate=new Spinner(this);String[] rateLabels={"2.5 Mbps · 省流畅用","4 Mbps · 720p 推荐","6 Mbps · 720p 更清晰","8 Mbps · 1080p 推荐","12 Mbps · 高清试验","24 Mbps · 高码率试验","自定义"};int[] rateValues={2500000,4000000,6000000,8000000,12000000,24000000};final int customRateIndex=rateValues.length;ArrayAdapter<String> rateChoices=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,rateLabels);rateChoices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);rate.setAdapter(rateChoices);box.addView(rate);
  int rememberedRate=saved.getInt("video_bit_rate",4000000),rateSelected=customRateIndex;for(int i=0;i<rateValues.length;i++)if(rememberedRate==rateValues[i])rateSelected=i;if(saved.getBoolean("custom_bitrate_selected",false))rateSelected=customRateIndex;
  EditText customRate=field(box,"自定义 0.5–40 Mbps，例如 5.5",java.math.BigDecimal.valueOf(rememberedRate,6).stripTrailingZeros().toPlainString());customRate.setInputType(InputType.TYPE_CLASS_NUMBER|InputType.TYPE_NUMBER_FLAG_DECIMAL);rate.setSelection(rateSelected);customRate.setVisibility(rateSelected==customRateIndex?View.VISIBLE:View.GONE);
    rate.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){customRate.setVisibility(position==customRateIndex?View.VISIBLE:View.GONE);try{bitRate=position<customRateIndex?rateValues[position]:Bitrate.parseMbps(customRate.getText().toString());saved.edit().putInt("video_bit_rate",bitRate).putBoolean("custom_bitrate_selected",position==customRateIndex).apply();}catch(IllegalArgumentException ignored){}}public void onNothingSelected(AdapterView<?> parent){}});
  customRate.addTextChangedListener(new android.text.TextWatcher(){public void beforeTextChanged(CharSequence text,int start,int count,int after){}public void onTextChanged(CharSequence text,int start,int before,int count){}public void afterTextChanged(android.text.Editable text){if(rate.getSelectedItemPosition()==customRateIndex)try{bitRate=Bitrate.parseMbps(text.toString());saved.edit().putInt("video_bit_rate",bitRate).putBoolean("custom_bitrate_selected",true).apply();}catch(IllegalArgumentException ignored){}}});
  TextView frameLabel=new TextView(this);frameLabel.setText("帧率上限（下次连接生效）");box.addView(frameLabel);Spinner frameRate=new Spinner(this);int[] frameValues={30,60,120};String[] frameLabels={"30 FPS · V50 默认均衡","60 FPS · 流畅高帧率","120 FPS · 高刷实验，需手机支持"};ArrayAdapter<String> frameChoices=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,frameLabels);frameChoices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);frameRate.setAdapter(frameChoices);frameRate.setSelection(maxFps==120?2:maxFps==60?1:0);box.addView(frameRate);frameRate.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){maxFps=frameValues[position];saved.edit().putInt("max_fps",maxFps).apply();}public void onNothingSelected(AdapterView<?> parent){}});
  TextView bufferLabel=new TextView(this);bufferLabel.setText("播放缓冲（下次连接生效）");box.addView(bufferLabel);
  Spinner playbackBuffer=new Spinner(this);int[] bufferValues={30,50,60,80,100};String[] bufferLabels={"30 ms · 极低延时试验","50 ms · 极速跟手 (高通推荐)","60 ms · 流畅跟手 (一加推荐)","80 ms · 延时与流畅度折中","100 ms · 流畅优先 · 多等约 20 ms"};playbackBuffer.setAdapter(new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,bufferLabels));int bufferSelected=2;for(int j=0;j<bufferValues.length;j++)if(bufferValues[j]==bufferMs)bufferSelected=j;playbackBuffer.setSelection(bufferSelected);box.addView(playbackBuffer);playbackBuffer.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){bufferMs=bufferValues[position];saved.edit().putInt("buffer_ms",bufferMs).apply();}public void onNothingSelected(AdapterView<?> parent){}});
  TextView rateHint=new TextView(this);rateHint.setText("V50 默认均衡：540P · H.264 · 网络自适应 VBR · 4 Mbps 目标上限 · 30 FPS · 80 ms 缓冲。可手动选 60 或 120 FPS，退出后记住最后设置；120 FPS 需要手机高刷屏、硬件解码和稳定网络。帧率为上限，实际帧率受远端应用内容影响。当前 M1 已启用苹果硬件编码；更高码率不直接提高帧率。卡顿或发热时可回到 V50 预设，一加测试不能代替 V50 验收。");rateHint.setTextSize(12);box.addView(rateHint);
  CheckBox soundEnabledChoice=new CheckBox(this);soundEnabledChoice.setText("开启远程声音");soundEnabledChoice.setChecked(soundEnabled);box.addView(soundEnabledChoice);soundEnabledChoice.setOnCheckedChangeListener((button,checked)->{soundEnabled=checked;saved.edit().putBoolean("sound_enabled",checked).apply();});
  CheckBox metricsChoice=new CheckBox(this);metricsChoice.setText("显示性能指标（延时、帧率、码率等）");metricsChoice.setChecked(saved.getBoolean("metrics_visible",false));box.addView(metricsChoice);metricsChoice.setOnCheckedChangeListener((button,checked)->saved.edit().putBoolean("metrics_visible",checked).apply());
  TextView soundLabel=new TextView(this);soundLabel.setText("声音增强（保留手机实体音量键控制）");box.addView(soundLabel);Spinner sound=new Spinner(this);String[] soundLabels={"原声 · 1 倍","轻度增强 · 1.5 倍","明显增强 · 2 倍","强增强 · 3 倍"};float[] gains={1f,1.5f,2f,3f};ArrayAdapter<String> soundChoices=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,soundLabels);soundChoices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);sound.setAdapter(soundChoices);int soundSelected=0;for(int i=0;i<gains.length;i++)if(gains[i]==audioGain)soundSelected=i;sound.setSelection(soundSelected);box.addView(sound);sound.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){audioGain=gains[position];saved.edit().putFloat("audio_gain",audioGain).apply();}public void onNothingSelected(AdapterView<?> parent){}});
  TextView syncLabel=new TextView(this);syncLabel.setText("音画同步校准（补偿扬声器或蓝牙耳机硬件延迟）");box.addView(syncLabel);Spinner syncSpinner=new Spinner(this);int[] syncOffsets={0,-25,-50,-80,25,50,80};String[] syncLabels={"测量基准 · 0 ms","手动 · 声音提前 25 ms","手动 · 声音提前 50 ms","手动 · 声音提前 80 ms","手动 · 声音延后 25 ms","手动 · 声音延后 50 ms","手动 · 声音延后 80 ms"};ArrayAdapter<String> syncChoices=new ArrayAdapter<>(this,android.R.layout.simple_spinner_item,syncLabels);syncChoices.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);syncSpinner.setAdapter(syncChoices);int syncSelected=0;for(int i=0;i<syncOffsets.length;i++)if(syncOffsets[i]==avSyncOffsetMs)syncSelected=i;syncSpinner.setSelection(syncSelected);box.addView(syncSpinner);syncSpinner.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener(){public void onItemSelected(AdapterView<?> parent,View view,int position,long id){avSyncOffsetMs=syncOffsets[position];saved.edit().putInt("av_sync_offset_ms",avSyncOffsetMs).apply();PlaybackClock clk=playback;if(clk!=null)clk.setAvSyncOffsetMs(avSyncOffsetMs);}public void onNothingSelected(AdapterView<?> parent){}});
  TextView dualTitle=new TextView(this);dualTitle.setText("专属双方案 · 点击一键切换（自动生效与记忆）");dualTitle.setTextSize(14);dualTitle.setTextColor(CuteUi.INK);dualTitle.setTypeface(null,android.graphics.Typeface.BOLD);dualTitle.setPadding(0,CuteUi.dp(this,8),0,CuteUi.dp(this,4));box.addView(dualTitle);
  LinearLayout profileActions=new LinearLayout(this);
  Button btnOnePlus=new Button(this);btnOnePlus.setText("方案一：一加 15\n60 FPS 上限 (高通)");CuteUi.style(btnOnePlus,CuteUi.MINT,true);profileActions.addView(btnOnePlus,new LinearLayout.LayoutParams(0,CuteUi.dp(this,56),1));
  Button btnRealme=new Button(this);btnRealme.setText("方案二：真我 V50\n30 FPS 上限 (联发科)");CuteUi.style(btnRealme,CuteUi.PINK,true);profileActions.addView(btnRealme,new LinearLayout.LayoutParams(0,CuteUi.dp(this,56),1));
  box.addView(profileActions);
  TextView profileHint=new TextView(this);profileHint.setTextSize(12);profileHint.setPadding(0,CuteUi.dp(this,4),0,CuteUi.dp(this,6));
  String curProfile=saved.getString("selected_profile",DeviceProfiles.detectDefaultProfile().id);
  profileHint.setText(curProfile.equals("realme_v50_30fps")?("当前生效：【"+DeviceProfiles.REALME_V50_30FPS.title+"】\n"+DeviceProfiles.REALME_V50_30FPS.summary):("当前生效：【"+DeviceProfiles.ONEPLUS_60FPS.title+"】\n"+DeviceProfiles.ONEPLUS_60FPS.summary));
  box.addView(profileHint);
  btnOnePlus.setOnClickListener(v->{
   DeviceProfiles.Profile p=DeviceProfiles.ONEPLUS_60FPS;
   quality.setSelection(1);
   rate.setSelection(p.bitrate==4000000?1:3);
   customRate.setVisibility(View.GONE);
   mode.setSelection(modeIndex(p.mode));
   maxFps=p.fps;frameRate.setSelection(1);
   bufferMs=p.bufferMs;
   for(int j=0;j<bufferValues.length;j++)if(bufferValues[j]==bufferMs)playbackBuffer.setSelection(j);
   audioGain=p.audioGain;sound.setSelection(0);
   avSyncOffsetMs=p.avSyncOffsetMs;for(int j=0;j<syncOffsets.length;j++)if(syncOffsets[j]==avSyncOffsetMs)syncSpinner.setSelection(j);
   metricsChoice.setChecked(p.showMetrics);
   saved.edit().putString("selected_profile",p.id).putInt("quality_max_size",p.maxSize).putInt("video_bit_rate",p.bitrate).putString("bitrate_mode",p.mode).putInt("max_fps",p.fps).putInt("buffer_ms",p.bufferMs).putFloat("audio_gain",p.audioGain).putInt("av_sync_offset_ms",p.avSyncOffsetMs).putBoolean("metrics_visible",p.showMetrics).putBoolean("custom_bitrate_selected",false).apply();
   profileHint.setText("已切换至【"+p.title+"】\n"+p.summary);
  });
  btnRealme.setOnClickListener(v->{
   DeviceProfiles.Profile p=DeviceProfiles.REALME_V50_30FPS;
   quality.setSelection(1);
   rate.setSelection(customRateIndex);
   customRate.setText(java.math.BigDecimal.valueOf(p.bitrate,6).stripTrailingZeros().toPlainString());
   customRate.setVisibility(View.VISIBLE);
   mode.setSelection(modeIndex(p.mode));
   maxFps=p.fps;frameRate.setSelection(0);
   bufferMs=p.bufferMs;
   for(int j=0;j<bufferValues.length;j++)if(bufferValues[j]==bufferMs)playbackBuffer.setSelection(j);
   audioGain=p.audioGain;sound.setSelection(1);
   avSyncOffsetMs=p.avSyncOffsetMs;for(int j=0;j<syncOffsets.length;j++)if(syncOffsets[j]==avSyncOffsetMs)syncSpinner.setSelection(j);
   metricsChoice.setChecked(p.showMetrics);
   saved.edit().putString("selected_profile",p.id).putInt("quality_max_size",p.maxSize).putInt("video_bit_rate",p.bitrate).putString("bitrate_mode",p.mode).putInt("max_fps",p.fps).putInt("buffer_ms",p.bufferMs).putFloat("audio_gain",p.audioGain).putInt("av_sync_offset_ms",p.avSyncOffsetMs).putBoolean("metrics_visible",p.showMetrics).putBoolean("custom_bitrate_selected",true).apply();
   profileHint.setText("已切换至【"+p.title+"】\n"+p.summary);
  });
  Button start=new Button(this);start.setText("连接虚拟安卓");CuteUi.style(start,CuteUi.MINT,false);box.addView(start,new LinearLayout.LayoutParams(-1,CuteUi.dp(this,60)));status=new TextView(this);status.setText("默认通过 NPS 公网连接。密码需点“保存密码”后才会加密保存。");box.addView(status);
  start.setOnClickListener(v->{
   final String loginAccount=user.getText().toString(),loginSecret=password.getText().toString();
   if(loginAccount.isEmpty()||loginSecret.isEmpty()){status.setText("请填写用户名和密码，或先保存密码。");return;}
   final String loginAuth="Basic "+Base64.encodeToString((loginAccount+":"+loginSecret).getBytes(StandardCharsets.UTF_8),2);
   try{host=Endpoint.destination(address.getText().toString());}catch(IllegalArgumentException e){status.setText(e.getMessage());return;}
   try{bitRate=rate.getSelectedItemPosition()<customRateIndex?rateValues[rate.getSelectedItemPosition()]:Bitrate.parseMbps(customRate.getText().toString());}catch(IllegalArgumentException e){status.setText(e.getMessage());return;}
   // Keep the user's field untouched: setText would fire password restoration
   // and replace an explicitly typed, unsaved secret during normalization.
   maxSize=sizes[quality.getSelectedItemPosition()];bitrateMode=modeName(mode.getSelectedItemPosition());
   saved.edit().putString("host",host).putString("username",loginAccount).putInt("quality_max_size",maxSize).putInt("video_bit_rate",bitRate).putString("bitrate_mode",bitrateMode).apply();
   auth=loginAuth;start.setEnabled(false);status.setText("连接中…");
   new Thread(()->{try{initTLS();String id=session();runOnUiThread(()->{password.setText("");show(id);});}catch(Exception e){auth=null;runOnUiThread(()->{status.setText("连接失败："+message(e));start.setEnabled(true);});}},"login").start();
  });
  Button diagnosticButton=new Button(this);diagnosticButton.setText("一键检测 · 自动提交报告");CuteUi.style(diagnosticButton,CuteUi.MINT,false);box.addView(diagnosticButton,new LinearLayout.LayoutParams(-1,CuteUi.dp(this,56)));diagnosticButton.setOnClickListener(v->{if(!start.isEnabled()){status.setText("正在连接，请等待当前连接完成后再开始检测。");return;}DiagnosticRunner.choose(this);});
  Button reportButton=new Button(this);reportButton.setText("上次检测报告 / 重新提交");CuteUi.style(reportButton,CuteUi.MINT,true);box.addView(reportButton,new LinearLayout.LayoutParams(-1,CuteUi.dp(this,48)));reportButton.setOnClickListener(v->{if(!start.isEnabled()){status.setText("正在连接，请等待当前连接完成后再查看报告。");return;}DiagnosticRunner.previous(this);});
  Button filesButton=new Button(this);filesButton.setText("照片 · 视频互传");CuteUi.style(filesButton,CuteUi.MINT,false);box.addView(filesButton,new LinearLayout.LayoutParams(-1,CuteUi.dp(this,56)));
  filesButton.setOnClickListener(v->{if(!start.isEnabled()){status.setText("正在连接，请等待当前连接完成后再打开文件。");return;}try{host=Endpoint.destination(address.getText().toString());initTLS();auth="Basic "+Base64.encodeToString((user.getText()+":"+password.getText()).getBytes(StandardCharsets.UTF_8),2);saved.edit().putString("host",host).putString("username",user.getText().toString()).apply();password.setText("");openFiles();}catch(Exception e){status.setText(message(e));}});
  ScrollView scroll=new ScrollView(this);scroll.setFillViewport(true);scroll.addView(box);
  scroll.setOnApplyWindowInsetsListener((v,insets)->{android.graphics.Insets i=insets.getInsets(WindowInsets.Type.systemBars());v.setPadding(i.left,i.top,i.right,i.bottom);return insets;});setContentView(scroll);restorePassword();updater.check(false);
 }
 boolean componentProbeRequested(){return ConnectionRoutes.componentProbeAllowed(BuildConfig.APPLICATION_ID,getIntent().getBooleanExtra("huoguo_codec_component_probe",false));}
 void restorePassword(){try{String destination=Endpoint.destination(address.getText().toString()),name=user.getText().toString();String secret=passwordStore.load(Endpoint.identity(destination),name);if(secret.isEmpty())for(String alias:ConnectionRoutes.passwordAliases(destination,name)){secret=passwordStore.load(alias,name);if(!secret.isEmpty())break;}password.setText(secret);}catch(IllegalArgumentException e){password.setText("");}catch(Exception e){password.setText("");if(status!=null)status.setText("已保存的密码无法读取，请重新输入并保存。");}}
 EditText field(LinearLayout b,String hint,String value){EditText f=new EditText(this);f.setSingleLine();f.setTextColor(CuteUi.INK);f.setHintTextColor(0xff719095);f.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xffaacfc5));f.setHint(hint);f.setText(value);b.addView(f);return f;}
 static String message(Exception e){return e.getMessage()==null?e.getClass().getSimpleName():e.getMessage();}
 void initTLS()throws Exception{KeyStore ks=KeyStore.getInstance(KeyStore.getDefaultType());ks.load(null,null);fingerprints.clear();int[] resources={R.raw.server_cert,R.raw.server_cert_m5};for(int i=0;i<resources.length;i++){X509Certificate cert;try(InputStream in=getResources().openRawResource(resources[i])){cert=(X509Certificate)CertificateFactory.getInstance("X.509").generateCertificate(in);}fingerprints.add(MessageDigest.getInstance("SHA-256").digest(cert.getEncoded()));ks.setCertificateEntry("mac"+i,cert);}TrustManagerFactory tm=TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());tm.init(ks);tls=SSLContext.getInstance("TLS");tls.init(null,tm.getTrustManagers(),null);}
 SSLSocket connect()throws Exception{return connect(host);}
 SSLSocket connect(String target)throws Exception{Endpoint.Address endpoint=Endpoint.parse(target);SSLSocket s=(SSLSocket)tls.getSocketFactory().createSocket();s.connect(new java.net.InetSocketAddress(endpoint.host,endpoint.port),7000);s.setSoTimeout(15000);s.setTcpNoDelay(true);s.startHandshake();byte[] actual=MessageDigest.getInstance("SHA-256").digest(s.getSession().getPeerCertificates()[0].getEncoded());boolean trusted=false;for(byte[] pin:fingerprints)trusted|=MessageDigest.isEqual(actual,pin);if(!trusted){s.close();throw new IOException("Mac 证书不匹配");}sockets.add(s);return s;}
 int header(InputStream in)throws Exception{ByteArrayOutputStream b=new ByteArrayOutputStream();int state=0;while(b.size()<16384){int c=in.read();if(c<0)throw new EOFException();b.write(c);if(state==0&&c==13)state=1;else if(state==1&&c==10)state=2;else if(state==2&&c==13)state=3;else if(state==3&&c==10)break;else state=0;}return Integer.parseInt(b.toString("US-ASCII").split("\\r\\n")[0].split(" ")[1]);}
 String session()throws Exception{SSLSocket s=connect();s.setSoTimeout(150000);byte[] settings=("{\"max_size\":"+maxSize+",\"video_bit_rate\":"+bitRate+",\"max_fps\":"+maxFps+",\"bitrate_mode\":\""+bitrateMode+"\"}").getBytes(StandardCharsets.UTF_8);OutputStream out=s.getOutputStream();out.write(("POST /session HTTP/1.1\r\nHost: "+host+"\r\nAuthorization: "+auth+"\r\nContent-Type: application/json\r\nContent-Length: "+settings.length+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));out.write(settings);InputStream in=s.getInputStream();int code=header(in);if(code!=200){s.close();throw new IOException(code==401?"登录被拒绝：请点“显示密码”核对用户名和特殊符号":code==429?"登录尝试过多，请稍后重试":code==400?"服务器不支持当前连接参数，请先改用 30 或 60 FPS 重试":"服务启动失败（"+code+"）");}ByteArrayOutputStream b=new ByteArrayOutputStream();byte[] buf=new byte[1024];int n;while((n=in.read(buf))>0)b.write(buf,0,n);s.close();sockets.remove(s);JSONObject info=new JSONObject(b.toString("UTF-8"));if(bitrateMode.equals("ADAPTIVE_VBR")&&!info.optBoolean("adaptive_vbr",false))throw new IOException("服务器尚未支持网络自适应 VBR，请先用 VBR");return info.getString("session");}
 SSLSocket stream(String id,String role)throws Exception{SSLSocket s=connect();s.getOutputStream().write(("CONNECT /stream/"+id+"/"+role+" HTTP/1.1\r\nHost: "+host+"\r\nAuthorization: "+auth+"\r\n\r\n").getBytes(StandardCharsets.US_ASCII));if(header(s.getInputStream())!=200)throw new IOException("通道连接失败："+role);s.setSoTimeout(0);return s;}
  void show(String id){presentationMetrics=new MediaPresentationMetrics(48000);adaptive=bitrateMode.equals("ADAPTIVE_VBR")?new AdaptiveBitrate(bitRate):null;acceptedBitrate=bitRate;adaptiveRejected=false;running=true;int gen=++generation;playback=new PlaybackClock(bufferMs,avSyncOffsetMs);networkRttMs=-1;LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setOnApplyWindowInsetsListener((v,insets)->{android.graphics.Insets i=insets.getInsets(WindowInsets.Type.systemBars());v.setPadding(i.left,i.top,i.right,i.bottom);return insets;});canvas=new FrameLayout(this);canvas.setBackgroundColor(0xff000000);root.addView(canvas,new LinearLayout.LayoutParams(-1,0,1));screen=new SurfaceView(this);canvas.addView(screen,new FrameLayout.LayoutParams(-1,-1,17));screen.setOnTouchListener((v,e)->{if(e.getActionMasked()==MotionEvent.ACTION_MOVE){long now=SystemClock.uptimeMillis();if(now-lastMoveMs<16)return true;lastMoveMs=now;}MotionEvent copy=MotionEvent.obtain(e);input.touch(copy.getActionMasked()==MotionEvent.ACTION_MOVE,gen,()->{if(running&&gen==generation)touch(copy,gen);},copy::recycle);return true;});int[] codes={4,3,187};LinearLayout bar=CuteUi.controls(this,j->{if(j<3)input.execute(()->{if(running&&gen==generation)key(codes[j],gen);});else if(j==3)toggleOrientation(gen);else if(j==4)openFiles();else{stop();login();}});Button rotateBtn=bar.findViewWithTag("rotate_btn");rotateButton=rotateBtn;if(rotateBtn!=null){boolean isLandscape=getResources().getConfiguration().orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE;rotateBtn.setText(isLandscape?"竖屏":"横屏");rotateBtn.setOnLongClickListener(v->{rotateRemoteDevice(generation);Toast.makeText(this,"已向远程安卓发送旋转指令",Toast.LENGTH_SHORT).show();return true;});}root.addView(bar);if(diagnostics!=null)bar.setVisibility(View.GONE);perf=new TextView(this);perf.setTextColor(0xffffffff);perf.setBackgroundColor(0x99000000);perf.setTextSize(11);perf.setPadding(8,4,8,4);canvas.addView(perf,new FrameLayout.LayoutParams(-2,-2,Gravity.TOP|Gravity.START));metricsVisible=getSharedPreferences("connection",0).getBoolean("metrics_visible",false);updateMetricsVisibility();if(diagnostics!=null)diagnostics.decorate(canvas);setContentView(root);WindowManager.LayoutParams windowParams=getWindow().getAttributes();windowParams.preferredRefreshRate=maxFps>=60?maxFps:(getSharedPreferences("connection",0).getBoolean("v50_profile",false)?60f:0f);getWindow().setAttributes(windowParams);canvas.post(this::fit);receivedFrames.set(0);receivedVideoBytes.set(0);presentedFrames.set(0);lateDiscardedFrames.set(0);audioOutputBytes.set(0);stats(gen);new Thread(()->measureRtt(gen),"network-rtt").start();new Thread(()->{try{SSLSocket vs=stream(id,"video"),as=stream(id,"audio"),cs=stream(id,"control");DataOutputStream nextControl=new DataOutputStream(new BufferedOutputStream(cs.getOutputStream(),8192));synchronized(MainActivity.this){if(!running||gen!=generation)return;control=nextControl;}new Thread(()->readAudio(as,gen),"audio").start();new Thread(()->readControlReplies(cs,gen),"control-replies").start();AdaptiveBitrate initialPlan=adaptive;if(initialPlan!=null)requestAdaptiveBitrate(initialPlan.target(),gen);readVideo(vs,gen);}catch(Exception e){if(running&&gen==generation)fail(e);}},"video").start();}


 static int modeIndex(String name){return name.equals("ADAPTIVE_VBR")?2:name.equals("VBR")?1:0;}
 static String modeName(int position){return position==2?"ADAPTIVE_VBR":position==1?"VBR":"CBR";}
 void requestAdaptiveBitrate(int target,int gen){synchronized(controlWriteLock){
  DataOutputStream writer=control;if(writer==null||!running||gen!=generation||adaptiveRejected)return;
  try{writer.writeByte(240);writer.writeInt(target);writer.flush();}catch(IOException e){adaptiveRejected=true;}
 }}
 void readControlReplies(SSLSocket socket,int gen){
  try{DataInputStream in=new DataInputStream(socket.getInputStream());while(running&&gen==generation){int type=in.readUnsignedByte();
   if(type==240){int rate=in.readInt();if(gen!=generation)return;if(rate>=500000&&rate<=bitRate)acceptedBitrate=rate;else{adaptiveRejected=true;runOnUiThread(()->Toast.makeText(this,"编码器未接受码率调整，已保持当前 VBR 目标",Toast.LENGTH_SHORT).show());}}
   else if(type==0){int length=in.readInt();if(length<0||length>262144)throw new IOException("无效控制回复");byte[] data=new byte[length];in.readFully(data);}
   else if(type==1)in.readLong();
   else if(type==2){in.readUnsignedShort();int length=in.readUnsignedShort();byte[] data=new byte[length];in.readFully(data);}
   else throw new IOException("不支持的控制回复");
  }}catch(Exception e){if(running&&gen==generation&&adaptive!=null&&!adaptiveRejected){adaptiveRejected=true;runOnUiThread(()->Toast.makeText(this,"自适应反馈中断，已停止自动调整，保持最后接受的码率",Toast.LENGTH_SHORT).show());}}
 }

 static boolean virtualCodec(String name){return name.startsWith("c2.goldfish.")||name.startsWith("OMX.google.goldfish.");}
 MediaCodec fastDecoder(MediaFormat format)throws Exception{
  MediaCodecInfo chosen=null;int score=-1;
  for(MediaCodecInfo info:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()){
   if(info.isEncoder()||info.isAlias()||!info.isHardwareAccelerated()||virtualCodec(info.getName()))continue;
   for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase("video/avc")){
    try{MediaCodecInfo.CodecCapabilities cap=info.getCapabilitiesForType(type);MediaFormat basic=MediaFormat.createVideoFormat("video/avc",width,height);
     if(!cap.isFormatSupported(basic)||cap.isFeatureRequired(MediaCodecInfo.CodecCapabilities.FEATURE_SecurePlayback)||cap.isFeatureRequired(MediaCodecInfo.CodecCapabilities.FEATURE_TunneledPlayback))continue;
     int candidate=cap.isFeatureSupported(MediaCodecInfo.CodecCapabilities.FEATURE_LowLatency)?2:1;if(candidate>score){score=candidate;chosen=info;}
    }catch(Exception ignored){}
   }
  }
  // Emulator host codecs may advertise hardware support but fail on the first frame.
  // Physical-phone hardware codecs retain priority; use a real software codec only as fallback.
  if(chosen==null)for(MediaCodecInfo info:new MediaCodecList(MediaCodecList.REGULAR_CODECS).getCodecInfos()){
   if(info.isEncoder()||info.isAlias()||!info.isSoftwareOnly())continue;
   for(String type:info.getSupportedTypes())if(type.equalsIgnoreCase("video/avc")){chosen=info;break;}
   if(chosen!=null)break;
  }
  MediaCodec decoder=chosen==null?MediaCodec.createDecoderByType("video/avc"):MediaCodec.createByCodecName(chosen.getName());hardwareVideo=decoder.getCodecInfo().isHardwareAccelerated();videoDecoderName=decoder.getName();return decoder;
 }
 void stats(int gen){final long[] previous={System.nanoTime(),0,0,0,0};ui.postDelayed(new Runnable(){public void run(){if(!running||gen!=generation)return;long now=System.nanoTime(),rx=receivedFrames.get(),shown=presentedFrames.get(),bytes=receivedVideoBytes.get(),late=lateDiscardedFrames.get();double dt=(now-previous[0])/1e9;perf.setText(String.format(java.util.Locale.ROOT,"接收 %.0f · 解码回调 %.0f FPS · 逾期丢帧 %.0f/秒\n%d×%d · %s\n网络往返 %s ms · 缓冲 %d ms\n%s · 目标 %.2f / 视频接收 %.2f Mbps%s",(rx-previous[1])/dt,(shown-previous[2])/dt,(late-previous[4])/dt,width,height,hardwareVideo?"硬件解码":"软件解码",networkRttMs<0?"—":Long.toString(networkRttMs),bufferMs,bitrateMode,acceptedBitrate/1e6,(bytes-previous[3])*8/dt/1e6,adaptiveRejected?"（调整失败）":""));android.util.Log.i("AndroidDirectTelemetry",String.format(java.util.Locale.ROOT,"t_ns=%d cap=%d rx=%.2f shown=%.2f late=%.2f rtt_ms=%d bitrate_target=%d video_mbps=%.3f hw_decode=%s",now,maxFps,(rx-previous[1])/dt,(shown-previous[2])/dt,(late-previous[4])/dt,networkRttMs,acceptedBitrate,(bytes-previous[3])*8/dt/1e6,hardwareVideo));previous[0]=now;previous[1]=rx;previous[2]=shown;previous[3]=bytes;previous[4]=late;ui.postDelayed(this,1000);}},1000);}

 void updateMetricsVisibility(){perf.setVisibility(metricsVisible?View.VISIBLE:View.GONE);}
 void fit(){if(canvas==null)return;int w=canvas.getWidth(),h=canvas.getHeight();if(w==0||h==0)return;float s=Math.min((float)w/width,(float)h/height);screen.setLayoutParams(new FrameLayout.LayoutParams(Math.round(width*s),Math.round(height*s),17));}
 synchronized void configure(int w,int h,int gen)throws Exception {
  if(!running||gen!=generation)return;
  width=w;height=h;runOnUiThread(this::fit);
  for(int i=0;i<100&&running&&gen==generation&&!screen.getHolder().getSurface().isValid();i++)Thread.sleep(20);
  if(!running||gen!=generation)return;
  MediaCodec old=video;video=null;if(old!=null){old.stop();old.release();}
  MediaFormat format=MediaFormat.createVideoFormat("video/avc",w,h);
  format.setInteger(MediaFormat.KEY_LOW_LATENCY,1);
  if(DeviceProfiles.isQualcomm())format.setInteger("vendor.qti-ext-dec-low-latency.enable",1);
  MediaCodec decoder=fastDecoder(format);Surface surface=screen.getHolder().getSurface();
  try{surface.setFrameRate(maxFps,Surface.FRAME_RATE_COMPATIBILITY_FIXED_SOURCE);}catch(IllegalStateException ignored){}
  decoder.configure(format,surface,null,0);decoder.start();video=decoder;
  MediaPresentationMetrics metrics=presentationMetrics;
  decoder.setOnFrameRenderedListener((codec,pts,ns)->{
   if(running&&gen==generation&&video==decoder){
    presentedFrames.incrementAndGet();
    if(metrics!=null)metrics.rendered(pts,ns,System.nanoTime());
    DiagnosticRunner test=diagnostics;if(test!=null)test.rendered(pts,ns);
   }
  },new Handler(statsThread.getLooper()));
  PlaybackClock clock=playback;final int submissionLeadMs=probeVideoSubmissionLeadMs;
  new Thread(()->{
   MediaCodec.BufferInfo info=new MediaCodec.BufferInfo(),nextInfo=new MediaCodec.BufferInfo();
   try{
    while(running&&gen==generation&&video==decoder){
     int index=decoder.dequeueOutputBuffer(info,10000);if(index<0)continue;
     long readyNs=System.nanoTime();boolean targetObserved=false,submissionWaited=false;
     while(running&&gen==generation&&video==decoder){
      long now=System.nanoTime(),target=probeImmediateVideoRelease?now
       :submissionLeadMs>0&&targetObserved?clock.deadline(info.presentationTimeUs):clock.videoDeadline(info.presentationTimeUs,now);
      targetObserved=true; // Repeating a park must not repeat decoder clock feedback or shift shared audio.
      if(target<now){
       int nextIndex=decoder.dequeueOutputBuffer(nextInfo,0);
       if(nextIndex>=0){
        if(metrics!=null)metrics.discarded(info.presentationTimeUs);
        decoder.releaseOutputBuffer(index,false);lateDiscardedFrames.incrementAndGet();
        DiagnosticRunner test=diagnostics;if(test!=null)test.discarded();
        index=nextIndex;info.set(nextInfo.offset,nextInfo.size,nextInfo.presentationTimeUs,nextInfo.flags);
        readyNs=System.nanoTime();targetObserved=false;submissionWaited=false;continue;
       }
       target=now;
      }
      if(!running||gen!=generation||video!=decoder){decoder.releaseOutputBuffer(index,false);break;}
      if(submissionLeadMs>0&&!probeImmediateVideoRelease){
       long parkNs=probeVideoSubmissionParkNs(target,now,readyNs,submissionLeadMs);
       if(parkNs>0){
        if(!submissionWaited){submissionWaited=true;probeVideoSubmissionWaitedOutputs.incrementAndGet();}
        long parkStarted=System.nanoTime();java.util.concurrent.locks.LockSupport.parkNanos(parkNs);
        long parkEnded=System.nanoTime(),actualParkNs=Math.max(0,parkEnded-parkStarted),heldNs=Math.max(0,parkEnded-readyNs);
        probeVideoSubmissionWaits.incrementAndGet();probeVideoSubmissionWaitNs.addAndGet(actualParkNs);
        probeVideoSubmissionMaxParkNs.accumulateAndGet(actualParkNs,Math::max);
        probeVideoSubmissionMaxHoldNs.accumulateAndGet(heldNs,Math::max);continue;
       }
       if(target-now>submissionLeadMs*1_000_000L&&now-readyNs>=80_000_000L)probeVideoSubmissionBudgetFallbacks.incrementAndGet();
      }
      if(metrics!=null)metrics.scheduled(info.presentationTimeUs,readyNs,target,System.nanoTime());
      decoder.releaseOutputBuffer(index,target);break;
     }
    }
   }catch(Exception e){if(running&&gen==generation&&video==decoder)fail(e);}
  },"video-render").start();
 }
 /** Pure probe-only wait budget; no clock mutation or target timestamp rewriting. */
 static long probeVideoSubmissionParkNs(long targetNs,long nowNs,long readyNs,int leadMs){
  if(leadMs!=8&&leadMs!=16||targetNs<=nowNs)return 0;
  long remaining=targetNs-nowNs-leadMs*1_000_000L;
  long holdRoom=80_000_000L-Math.max(0,nowNs-readyNs);
  return remaining<=0||holdRoom<=0?0:Math.min(2_000_000L,Math.min(remaining,holdRoom));
 }
 void readVideo(SSLSocket s,int gen)throws Exception{MediaPresentationMetrics metrics=presentationMetrics;DataInputStream in=new DataInputStream(new BufferedInputStream(s.getInputStream(),65536));if(in.readInt()!=0x68323634)throw new IOException("不支持的视频编码");while(running&&gen==generation){int hi=in.readInt();if(!running||gen!=generation)return;if((hi&0x80000000)!=0){int w=in.readInt(),h=in.readInt();if(!running||gen!=generation)return;if(w<1||h<1||w>8192||h>8192)throw new IOException("无效画面尺寸");configure(w,h,gen);continue;}long pts=((long)hi<<32)|(in.readInt()&0xffffffffL);int size=in.readInt();if(size<0||size>8*1024*1024)throw new IOException("无效视频帧");byte[] b=new byte[size];in.readFully(b);if(!running||gen!=generation)return;receivedVideoBytes.addAndGet(size);DiagnosticRunner test=diagnostics;if(test!=null)test.received(size,pts&((1L<<61)-1),System.nanoTime(),(pts&(1L<<62))!=0);if((pts&(1L<<62))==0){receivedFrames.incrementAndGet();long arrival=System.nanoTime();long sourcePts=pts&((1L<<61)-1);playback.observe(sourcePts,arrival);if(metrics!=null)metrics.received(sourcePts,arrival);AdaptiveBitrate plan=adaptive;if(plan!=null)plan.packet(sourcePts,arrival);}MediaCodec d=video;if(d==null)throw new IOException("缺少画面尺寸");int index=-1;while(running&&gen==generation&&(index=d.dequeueInputBuffer(10000))<0){}if(index<0)break;ByteBuffer buffer=d.getInputBuffer(index);if(buffer==null||buffer.capacity()<size)throw new IOException("解码输入缓冲不足，请降低清晰度后重新连接");buffer.clear();buffer.put(b);if(metrics!=null&&(pts&(1L<<62))==0)metrics.inputQueued(pts&((1L<<61)-1),System.nanoTime());d.queueInputBuffer(index,0,size,pts&((1L<<61)-1),(pts&(1L<<62))!=0?MediaCodec.BUFFER_FLAG_CODEC_CONFIG:0);}}
 void readAudio(SSLSocket s,int gen){try{DataInputStream in=new DataInputStream(new BufferedInputStream(s.getInputStream(),16384));int codec=in.readInt();if(codec==0||codec==1)return;if(codec!=0x00616163)throw new IOException("不支持的音频编码");while(running&&gen==generation){long pts=in.readLong();int size=in.readInt();if(size<0||size>1024*1024)throw new IOException("无效音频帧");byte[] data=new byte[size];in.readFully(data);if((pts&(1L<<62))!=0){MediaCodec oldAudio=audio;audio=null;if(oldAudio!=null){try{oldAudio.stop();oldAudio.release();}catch(IllegalStateException ignored){}}AudioTrack oldTrack=track;track=null;if(oldTrack!=null){try{oldTrack.stop();oldTrack.release();}catch(IllegalStateException ignored){}}MediaPresentationMetrics metrics=presentationMetrics;int audioEpoch=metrics==null?0:metrics.resetAudio();MediaFormat f=MediaFormat.createAudioFormat("audio/mp4a-latm",48000,2);f.setInteger(MediaFormat.KEY_PCM_ENCODING,AudioFormat.ENCODING_PCM_16BIT);f.setByteBuffer("csd-0",ByteBuffer.wrap(data));MediaCodec d=MediaCodec.createDecoderByType("audio/mp4a-latm");d.configure(f,null,null,0);d.start();audio=d;int min=AudioTrack.getMinBufferSize(48000,12,2);track=new AudioTrack.Builder().setAudioFormat(new AudioFormat.Builder().setSampleRate(48000).setChannelMask(AudioFormat.CHANNEL_OUT_STEREO) .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build()).setAudioAttributes(new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).setContentType(AudioAttributes.CONTENT_TYPE_MOVIE).build()).setTransferMode(AudioTrack.MODE_STREAM).setBufferSizeInBytes(Math.max(min,4096)).setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY).build();if(probeAudioBufferFrames>0)track.setBufferSizeInFrames(probeAudioBufferFrames);track.setVolume(soundEnabled?1f:0f);track.play();new Thread(()->drainAudio(d,gen,audioEpoch),"audio-render").start();continue;}playback.observeAudio(pts&((1L<<61)-1),System.nanoTime());MediaCodec d=audio;if(d==null)continue;int index=-1;while(running&&gen==generation&&(index=d.dequeueInputBuffer(10000))<0){}if(index<0)break;ByteBuffer buf=d.getInputBuffer(index);buf.clear();buf.put(data);d.queueInputBuffer(index,0,size,pts&((1L<<61)-1),0);}}catch(Exception e){if(running&&gen==generation)runOnUiThread(()->Toast.makeText(this,"音频暂不可用："+message(e),Toast.LENGTH_LONG).show());}}
 void drainAudio(MediaCodec decoder,int gen,int audioEpoch){
  AudioTrack output=track;PlaybackClock clock=playback;MediaPresentationMetrics metrics=presentationMetrics;
  long writtenFrames=0;ByteBuffer amplified=ByteBuffer.allocateDirect(16384);
  AudioTimestamp stamp=new AudioTimestamp();
  try{
   MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();
   while(running&&gen==generation&&audio==decoder){
    int index=decoder.dequeueOutputBuffer(info,10000);if(index<0)continue;
    ByteBuffer pcm=decoder.getOutputBuffer(index);pcm.position(info.offset);pcm.limit(info.offset+info.size);
    float gain=audioGain;
    if(gain!=1f){if(amplified.capacity()<pcm.remaining())amplified=ByteBuffer.allocateDirect(pcm.remaining());PcmGain.process(pcm,amplified,gain);pcm=amplified;}
    long target=clock.audioDeadline(info.presentationTimeUs);
    while(running&&gen==generation&&audio==decoder){
     long now=System.nanoTime();boolean valid=output.getTimestamp(stamp);long dacEstimate;
     if(metrics!=null)metrics.audioTimestamp(audioEpoch,valid,stamp.framePosition,stamp.nanoTime,now,
             output.getPlayState()==AudioTrack.PLAYSTATE_PLAYING);
     MediaPresentationMetrics.AudioEstimate estimate=metrics==null?null:metrics.audioEstimate(now);
     long timestampTail=valid&&output.getPlayState()==AudioTrack.PLAYSTATE_PLAYING&&estimate!=null&&estimate.valid
             ?AudioSubmissionClock.timestampQueueTailNs(now,stamp.nanoTime,writtenFrames,estimate.timestampFramePosition,48000)
             :AudioSubmissionClock.UNAVAILABLE;
     if(timestampTail!=AudioSubmissionClock.UNAVAILABLE)dacEstimate=timestampTail;
     else{long queued=Math.max(0,writtenFrames-(output.getPlaybackHeadPosition()&0xffffffffL));dacEstimate=now+queued*1000000000L/48000L+25_000_000L;}
     long wait=target-dacEstimate;if(wait<=2000000L)break;
     Thread.sleep(Math.min(10,Math.max(1,wait/1000000L)));
    }
    long chunkFrames=0;
    while(pcm.hasRemaining()&&running&&gen==generation&&audio==decoder){
     int bytes=output.write(pcm,pcm.remaining(),AudioTrack.WRITE_BLOCKING);
     if(bytes<0||bytes%4!=0)throw new IOException("音频输出失败");
     if(bytes==0){Thread.sleep(1);continue;}
     long frames=bytes/4;
     if(metrics!=null)metrics.audioWritten(audioEpoch,writtenFrames,frames,info.presentationTimeUs+chunkFrames*1000000L/48000L);
     chunkFrames+=frames;writtenFrames+=frames;audioOutputBytes.addAndGet(bytes);
    }
    decoder.releaseOutputBuffer(index,false);
   }
  }catch(Exception e){if(running&&gen==generation&&audio==decoder)runOnUiThread(()->Toast.makeText(this,"音频播放暂停："+message(e),Toast.LENGTH_LONG).show());}
 }
 void measureRtt(int gen){while(running&&gen==generation){SSLSocket socket=null;try{socket=connect();long start=System.nanoTime();socket.getOutputStream().write(("GET /ping HTTP/1.1\r\nHost: "+host+"\r\nConnection: close\r\n\r\n").getBytes(StandardCharsets.US_ASCII));if(header(socket.getInputStream())==200&&running&&gen==generation)networkRttMs=Math.max(1,(System.nanoTime()-start)/1000000L);else networkRttMs=-1;}catch(Exception ignored){if(gen==generation)networkRttMs=-1;}finally{if(socket!=null){sockets.remove(socket);try{socket.close();}catch(Exception ignored){}}}DiagnosticRunner test=diagnostics;if(test!=null&&running&&gen==generation)test.rtt(networkRttMs);AdaptiveBitrate plan=adaptive;if(plan!=null&&!adaptiveRejected&&running&&gen==generation){int target=plan.update(networkRttMs);if(target!=acceptedBitrate)requestAdaptiveBitrate(target,gen);}try{Thread.sleep(3000);}catch(InterruptedException e){return;}}}
 void touch(MotionEvent e,int gen){synchronized(controlWriteLock){DataOutputStream writer=control;if(writer==null||!running||gen!=generation)return;try{int a=e.getActionMasked();if(a==2){for(int i=0;i<e.getPointerCount();i++)pointer(writer,e,i,2);}else if(a==0||a==5)pointer(writer,e,e.getActionIndex(),0);else if(a==1||a==6)pointer(writer,e,e.getActionIndex(),1);else if(a==3){for(int i=0;i<e.getPointerCount();i++)pointer(writer,e,i,1);}writer.flush();}catch(Exception ex){if(running&&gen==generation)fail(ex);}}}
 void pointer(DataOutputStream writer,MotionEvent e,int i,int a)throws IOException{int x=Math.max(0,Math.min(width-1,Math.round(e.getX(i)*width/screen.getWidth()))),y=Math.max(0,Math.min(height-1,Math.round(e.getY(i)*height/screen.getHeight())));writer.writeByte(2);writer.writeByte(a);writer.writeLong(e.getPointerId(i));writer.writeInt(x);writer.writeInt(y);writer.writeShort(width);writer.writeShort(height);writer.writeShort(a==1?0:65535);writer.writeInt(0);writer.writeInt(0);}
 void key(int code,int gen){synchronized(controlWriteLock){DataOutputStream writer=control;if(writer==null||!running||gen!=generation)return;try{for(int a=0;a<2;a++){writer.writeByte(0);writer.writeByte(a);writer.writeInt(code);writer.writeInt(0);writer.writeInt(0);}writer.flush();}catch(Exception e){if(running&&gen==generation)fail(e);}}}
 void fail(Exception e){if(lanUdpEntry!=null&&lanUdpEntry.active()){lanUdpEntry.failed(e);return;}int failedGeneration=generation;runOnUiThread(()->{if(!running||failedGeneration!=generation)return;if(diagnostics!=null){diagnostics.streamFailed(e);return;}stop();login();status.setText("连接中断："+message(e));});}
 void rotateRemoteDevice(int gen){input.execute(()->{synchronized(controlWriteLock){DataOutputStream writer=control;if(writer==null||!running||gen!=generation)return;try{writer.writeByte(11);writer.flush();}catch(Exception e){if(running&&gen==generation)fail(e);}}});}
 void toggleOrientation(int gen){boolean isLandscape=getResources().getConfiguration().orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE;if(isLandscape){setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT);if(width>height)rotateRemoteDevice(gen);}else{setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE);if(width<height)rotateRemoteDevice(gen);}}
 @Override public void onConfigurationChanged(android.content.res.Configuration newConfig){super.onConfigurationChanged(newConfig);if(rotateButton!=null){rotateButton.setText(newConfig.orientation==android.content.res.Configuration.ORIENTATION_LANDSCAPE?"竖屏":"横屏");}if(canvas!=null){canvas.post(this::fit);}}
 synchronized void stop(){runOnUiThread(()->setRequestedOrientation(android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT));rotateButton=null;adaptive=null;running=false;generation++;input.clear();control=null;auth=null;synchronized(sockets){for(SSLSocket s:sockets)try{s.close();}catch(Exception ignored){}sockets.clear();}MediaCodec v=video,a=audio;video=null;audio=null;for(MediaCodec d:new MediaCodec[]{v,a})if(d!=null)try{d.stop();d.release();}catch(Exception ignored){}if(track!=null){try{track.stop();track.release();}catch(Exception ignored){}track=null;}}
 void openFiles(){boolean resume=running;String credential=auth;stop();auth=credential;transfer=new MediaTransfer(this,()->{transfer=null;if(resume){new Thread(()->{try{String id=session();runOnUiThread(()->show(id));}catch(Exception e){runOnUiThread(()->{auth=null;login();status.setText("重新连接失败："+message(e));});}},"resume-after-files").start();}else{auth=null;login();}});}
 public void onActivityResult(int request,int code,android.content.Intent data){super.onActivityResult(request,code,data);if(transfer!=null)transfer.result(request,code,data);}
 // API33+ is registered with the platform dispatcher above; this fallback serves API30-32.
 @android.annotation.SuppressLint("GestureBackNavigation")
 public void onBackPressed(){handleBack();}
 void handleBack(){if(lanUdpEntry!=null&&lanUdpEntry.active()){lanUdpEntry.cancel(true);return;}if(diagnostics!=null){if(diagnostics.finished)diagnostics.leave();else diagnostics.cancel();return;}if(transfer!=null){transfer.back();return;}if(running){int gen=generation;input.execute(()->{if(running&&gen==generation)key(4,gen);});}else finish();}
 protected void onResume(){super.onResume();if(!BuildConfig.AUTHENTICATED_LAN_UDP&&!componentProbeRequested()&&updater!=null)updater.resumeInstall();}
 protected void onStop(){if(lanUdpEntry!=null&&lanUdpEntry.active())lanUdpEntry.cancel(true);if(diagnostics!=null&&!diagnostics.finished)diagnostics.cancel();super.onStop();}
 protected void onDestroy(){if(lanUdpEntry!=null)lanUdpEntry.cancel(false);if(diagnostics!=null)diagnostics.close();if(transfer!=null)transfer.close();stop();input.shutdownNow();statsThread.quitSafely();super.onDestroy();}
}
