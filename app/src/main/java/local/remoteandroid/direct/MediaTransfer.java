package local.remoteandroid.direct;

import android.app.AlertDialog;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.OpenableColumns;
import android.view.Gravity;
import android.view.WindowInsets;
import android.widget.*;
import org.json.*;
import javax.net.ssl.SSLSocket;
import java.io.*;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.*;
import java.util.concurrent.*;

/** Photo/video exchange through the same authenticated, pinned TLS gateway. */
final class MediaTransfer {
    static final int PICK=701, SAVE=702;
    static final long MAX=1024L*1024*1024;
    final MainActivity activity;
    final Runnable finish;
    final ExecutorService worker=Executors.newSingleThreadExecutor();
    final String host,auth;
    volatile boolean closed;
    volatile SSLSocket connection;
    boolean busy;
    TextView status;
    ProgressBar progress;
    LinearLayout files;
    Button upload,refresh;
    JSONObject pending;

    MediaTransfer(MainActivity activity,Runnable finish){this.activity=activity;this.finish=finish;host=activity.host;auth=activity.auth;show();refresh();}
    void show(){
        LinearLayout root=new LinearLayout(activity);root.setOrientation(LinearLayout.VERTICAL);root.setBackgroundColor(0xfff5fbf9);
        root.setPadding(dp(16),dp(12),dp(16),dp(8));
        root.setOnApplyWindowInsetsListener((view,insets)->{android.graphics.Insets i=insets.getInsets(WindowInsets.Type.systemBars());view.setPadding(dp(16)+i.left,dp(12)+i.top,dp(16)+i.right,dp(8)+i.bottom);return insets;});
        TextView title=new TextView(activity);title.setText("照片 · 视频互传");title.setTextSize(23);title.setTextColor(CuteUi.INK);root.addView(title);
        TextView hint=new TextView(activity);hint.setText("互传时暂停远程画面，给照片和视频留出带宽。\n手机发来的文件会出现在安卓相册与 Download/火锅互传。\n支持局域网和外网，单个文件最多 1 GB；中断后请重新传送。");hint.setTextColor(CuteUi.INK);hint.setTextSize(13);root.addView(hint);
        LinearLayout buttons=new LinearLayout(activity);upload=button("手机 → 安卓",()->pick());refresh=button("刷新列表",this::refresh);buttons.addView(upload,new LinearLayout.LayoutParams(0,dp(56),1));buttons.addView(refresh,new LinearLayout.LayoutParams(0,dp(56),1));root.addView(buttons);
        status=new TextView(activity);status.setTextColor(CuteUi.INK);root.addView(status);progress=new ProgressBar(activity,null,android.R.attr.progressBarStyleHorizontal);progress.setMax(1000);root.addView(progress);
        ScrollView scroll=new ScrollView(activity);files=new LinearLayout(activity);files.setOrientation(LinearLayout.VERTICAL);scroll.addView(files);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        Button back=button("回到连接 / 安卓",this::back);CuteUi.style(back,CuteUi.PINK,false);root.addView(back,new LinearLayout.LayoutParams(-1,dp(56)));activity.setContentView(root);
    }
    int dp(int value){return CuteUi.dp(activity,value);}
    Button button(String text,Runnable action){Button button=new Button(activity);button.setText(text);CuteUi.style(button,CuteUi.MINT,true);button.setOnClickListener(v->action.run());return button;}
    void update(String text,long done,long total){activity.runOnUiThread(()->{if(closed)return;status.setText(text);progress.setProgress(total>0?(int)Math.min(1000,done*1000/total):0);});}
    void task(Callable<String> operation){
        if(busy||closed)return;busy=true;upload.setEnabled(false);refresh.setEnabled(false);progress.setProgress(0);
        worker.execute(()->{String message;try{message=operation.call();}catch(Exception e){message="传送失败："+MainActivity.message(e);}finally{closeConnection();}
            final String result=message;activity.runOnUiThread(()->{if(closed)return;busy=false;upload.setEnabled(true);refresh.setEnabled(true);status.setText(result);});});
    }
    void refresh(){task(()->{JSONObject response=json("GET","/files",null,null);JSONArray entries=response.getJSONArray("files");activity.runOnUiThread(()->{if(closed)return;files.removeAllViews();if(entries.length()==0){TextView empty=new TextView(activity);empty.setText("还没有照片或视频，点“手机 → 安卓”发一张试试。 ");files.addView(empty);}for(int i=0;i<entries.length();i++){final JSONObject item=entries.optJSONObject(i);if(item==null)continue;LinearLayout row=new LinearLayout(activity);row.setOrientation(LinearLayout.VERTICAL);row.setPadding(dp(8),dp(6),dp(8),dp(6));TextView name=new TextView(activity);name.setText(item.optString("name"));name.setTextColor(CuteUi.INK);name.setTextSize(15);row.addView(name);TextView info=new TextView(activity);info.setText(String.format(Locale.ROOT,"%.1f MB · %s",item.optLong("size")/1048576.0,item.optString("folder")));info.setTextSize(11);row.addView(info);Button save=button("保存到手机",()->save(item));row.addView(save,new LinearLayout.LayoutParams(-1,dp(48)));files.addView(row);}});return "共 "+entries.length()+" 个文件"+(response.optBoolean("truncated")?"（只显示前 500 个）":"")+" · 点保存选择手机文件夹";});}
    void pick(){if(busy)return;Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT);intent.setType("*/*");intent.putExtra(Intent.EXTRA_MIME_TYPES,new String[]{"image/*","video/*"});intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,true);intent.addCategory(Intent.CATEGORY_OPENABLE);activity.startActivityForResult(intent,PICK);}
    void save(JSONObject item){if(busy)return;pending=item;Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT);intent.setType(mime(item.optString("name")));intent.addCategory(Intent.CATEGORY_OPENABLE);intent.putExtra(Intent.EXTRA_TITLE,item.optString("name"));activity.startActivityForResult(intent,SAVE);}
    static String mime(String name){String n=name.toLowerCase(Locale.ROOT);if(n.endsWith(".png"))return "image/png";if(n.endsWith(".jpg")||n.endsWith(".jpeg"))return "image/jpeg";if(n.endsWith(".webp"))return "image/webp";if(n.endsWith(".heic"))return "image/heic";if(n.endsWith(".gif"))return "image/gif";if(n.endsWith(".mp4"))return "video/mp4";if(n.endsWith(".mov"))return "video/quicktime";return "application/octet-stream";}
    void result(int request,int code,Intent data){
        if(closed||code!=android.app.Activity.RESULT_OK||data==null)return;
        if(request==PICK){List<Uri> uris=new ArrayList<>();if(data.getClipData()!=null){for(int i=0;i<data.getClipData().getItemCount()&&i<20;i++)uris.add(data.getClipData().getItemAt(i).getUri());}else if(data.getData()!=null)uris.add(data.getData());
            task(()->{int done=0;for(Uri uri:uris){if(closed)throw new IOException("已取消");upload(uri,done+1,uris.size());done++;}return "已发送 "+done+" 个文件到安卓相册，点刷新查看。";});
        }else if(request==SAVE&&pending!=null&&data.getData()!=null){final Uri target=data.getData();final JSONObject item=pending;pending=null;task(()->{download(item,target);return "已保存到手机："+item.optString("name");});}
    }
    void upload(Uri uri,int index,int count)throws Exception{
        String name="";long size=-1;
        try(Cursor cursor=activity.getContentResolver().query(uri,new String[]{OpenableColumns.DISPLAY_NAME,OpenableColumns.SIZE},null,null,null)){if(cursor!=null&&cursor.moveToFirst()){name=cursor.getString(0);if(!cursor.isNull(1))size=cursor.getLong(1);}}
        File cache=null;InputStream in=null;
        try{
            if(name==null||name.isEmpty())throw new IOException("文件没有名称，请从相册或文件管理器重新选择");
            in=activity.getContentResolver().openInputStream(uri);if(in==null)throw new IOException("无法读取所选文件");
            if(size<0){cache=File.createTempFile("media-send-",".tmp",activity.getCacheDir());try(OutputStream local=new FileOutputStream(cache)){size=copy(in,local,MAX,-1,"准备文件");}in.close();in=new FileInputStream(cache);}
            if(size<=0||size>MAX)throw new IOException("单个文件须小于或等于 1 GB");
            SSLSocket socket=open();OutputStream out=socket.getOutputStream();request(out,"POST","/files",size,"X-File-Name: "+URLEncoder.encode(name,"UTF-8").replace("+","%20")+"\r\nContent-Type: application/octet-stream\r\n");
            java.security.MessageDigest digest=java.security.MessageDigest.getInstance("SHA-256");in=new java.security.DigestInputStream(in,digest);copy(in,out,MAX,size,"发送 "+index+"/"+count+" · "+name);out.flush();Response response=readHeader(socket.getInputStream());check(response);JSONObject answer=readJson(socket.getInputStream(),response.length);if(answer.optLong("size")!=size)throw new IOException("服务器文件大小不符");StringBuilder hash=new StringBuilder();for(byte b:digest.digest())hash.append(String.format(Locale.ROOT,"%02x",b&255));if(!hash.toString().equals(answer.optString("sha256")))throw new IOException("文件校验不一致，请重新传送");
        }finally{if(in!=null)in.close();if(cache!=null)cache.delete();closeConnection();}
    }
    void download(JSONObject item,Uri destination)throws Exception{
        boolean success=false;
        try{
            SSLSocket socket=open();request(socket.getOutputStream(),"GET","/files/"+item.getString("id"),-1,"");Response response=readHeader(socket.getInputStream());check(response);if(response.length<=0||response.length>MAX)throw new IOException("文件大小超出限制");
            try(OutputStream out=activity.getContentResolver().openOutputStream(destination,"wt")){if(out==null)throw new IOException("不能写入手机文件夹");copy(socket.getInputStream(),out,MAX,response.length,"保存 · "+item.optString("name"));}success=true;
        }finally{
            closeConnection();if(!success)try{android.provider.DocumentsContract.deleteDocument(activity.getContentResolver(),destination);}catch(Exception ignored){}
        }
    }
    long copy(InputStream in,OutputStream out,long limit,long expected,String label)throws IOException{
        byte[] buffer=new byte[65536];long total=0,last=0;while(expected<0||total<expected){if(closed)throw new IOException("已取消");int n=in.read(buffer,0,(int)Math.min(buffer.length,expected<0?buffer.length:expected-total));if(n<0)break;total+=n;if(total>limit)throw new IOException("文件超过 1 GB");out.write(buffer,0,n);long now=android.os.SystemClock.uptimeMillis();if(now-last>200){update(label+String.format(Locale.ROOT," · %.1f MB",total/1048576.0),total,expected);last=now;}}
        if(expected>=0&&total!=expected)throw new IOException("传送中断，请重新传送");update(label+" · 完成",total,total);return total;
    }
    JSONObject json(String method,String path,byte[] body,String extra)throws Exception{SSLSocket socket=open();request(socket.getOutputStream(),method,path,body==null?-1:body.length,extra==null?"":extra);if(body!=null)socket.getOutputStream().write(body);Response response=readHeader(socket.getInputStream());check(response);return readJson(socket.getInputStream(),response.length);}
    SSLSocket open()throws Exception{if(closed)throw new IOException("已取消");SSLSocket socket=activity.connect(host);socket.setSoTimeout(120000);connection=socket;if(closed){closeConnection();throw new IOException("已取消");}return socket;}
    void request(OutputStream out,String method,String path,long length,String extra)throws IOException{out.write((method+" "+path+" HTTP/1.1\r\nHost: "+host+"\r\nAuthorization: "+auth+"\r\nConnection: close\r\n"+(length>=0?"Content-Length: "+length+"\r\n":"")+extra+"\r\n").getBytes(StandardCharsets.US_ASCII));out.flush();}
    static final class Response{int code;long length=-1;}
    static Response readHeader(InputStream in)throws IOException{ByteArrayOutputStream bytes=new ByteArrayOutputStream();int state=0;boolean complete=false;while(bytes.size()<16384){int c=in.read();if(c<0)throw new EOFException();bytes.write(c);if(state==0&&c==13)state=1;else if(state==1&&c==10)state=2;else if(state==2&&c==13)state=3;else if(state==3&&c==10){complete=true;break;}else state=0;}if(!complete)throw new IOException("响应头过长");String[] lines=bytes.toString("US-ASCII").split("\r\n");Response response=new Response();try{response.code=Integer.parseInt(lines[0].split(" ")[1]);for(String line:lines)if(line.toLowerCase(Locale.ROOT).startsWith("content-length:"))response.length=Long.parseLong(line.substring(15).trim());}catch(RuntimeException e){throw new IOException("无效的服务器响应");}return response;}
    static JSONObject readJson(InputStream in,long length)throws Exception{if(length<0||length>1024*1024)throw new IOException("无效的文件列表");byte[] data=new byte[(int)length];new DataInputStream(in).readFully(data);return new JSONObject(new String(data,StandardCharsets.UTF_8));}
    void check(Response response)throws IOException{if(response.code>=200&&response.code<300)return;throw new IOException(response.code==401?"用户名或密码不正确，请返回重新填写":response.code==409?"服务器正在传另一个文件，请稍后重试":response.code==429?"登录次数过多，稍后重试":response.code==415?"仅支持照片和视频文件":response.code==413?"文件超过 1 GB":"服务器返回 "+response.code);}
    void closeConnection(){SSLSocket socket=connection;connection=null;if(socket!=null){activity.sockets.remove(socket);try{socket.close();}catch(IOException ignored){}}}
    void back(){if(busy)new AlertDialog.Builder(activity).setMessage("文件还在传送，停止并返回吗？").setNegativeButton("继续传送",null).setPositiveButton("停止并返回",(d,w)->leave()).show();else leave();}
    void leave(){close();finish.run();}
    void close(){closed=true;closeConnection();worker.shutdownNow();}
}
