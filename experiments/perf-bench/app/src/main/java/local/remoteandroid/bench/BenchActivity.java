package local.remoteandroid.bench;
import android.app.Activity;import android.os.Bundle;import android.graphics.*;import android.view.*;
public class BenchActivity extends Activity {
 private Motion view;
 @Override public void onCreate(Bundle b){super.onCreate(b);getWindow().getDecorView().setSystemUiVisibility(5894|1024|512);view=new Motion();setContentView(view);}
 @Override protected void onResume(){super.onResume();view.running=true;view.postInvalidateOnAnimation();}
 @Override protected void onPause(){view.running=false;super.onPause();}
 private final class Motion extends View{
  boolean running;long frame=0;final Paint p=new Paint();final Bitmap texture;
  Motion(){super(BenchActivity.this);texture=Bitmap.createBitmap(64,64,Bitmap.Config.ARGB_8888);for(int y=0;y<64;y++)for(int x=0;x<64;x++){int n=(x*67+y*131+(x*y)%47)&255;texture.setPixel(x,y,Color.rgb(n,(n*3)&255,(255-n)));}p.setFilterBitmap(false);}
  @Override protected void onDraw(Canvas c){super.onDraw(c);int w=getWidth(),h=getHeight();c.drawColor(Color.BLACK);int shift=(int)((frame*7)%64);p.setShader(new BitmapShader(texture,Shader.TileMode.REPEAT,Shader.TileMode.REPEAT));Matrix m=new Matrix();m.setTranslate(0,shift);p.getShader().setLocalMatrix(m);c.drawRect(0,0,w,h,p);p.setShader(null);p.setColor(Color.rgb(15,15,15));c.drawRect(0,h/2f-20,w,h/2f+20,p);frame++;if(running)postInvalidateOnAnimation();}
 }
}
