package local.huoguo.touchreceipt;
import android.app.Activity;
import android.os.Bundle;
import android.view.MotionEvent;
import android.view.View;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.FileOutputStream;
/** Separate guest test surface: records only injected test coordinates/actions. */
public final class TouchReceiptActivity extends Activity{
 final JSONArray events=new JSONArray();
 public void onCreate(Bundle state){super.onCreate(state);deleteFile("touch-receipt.json");
  getWindow().setDecorFitsSystemWindows(false);
  setContentView(new View(this){
  final Paint paint=new Paint();int tick;
  protected void onDraw(Canvas c){c.drawColor(0xff143442);paint.setColor(Color.WHITE);paint.setTextSize(38);c.drawText("Native UDP touch receipt · test",24,100,paint);
   c.drawCircle(24+(tick++%200)*3,150,12,paint);postInvalidateDelayed(16);}
  public boolean onTouchEvent(MotionEvent e){try{
   if(events.length()<64){JSONArray points=new JSONArray();for(int i=0;i<e.getPointerCount();i++)points.put(new JSONObject().put("id",e.getPointerId(i)).put("x",e.getX(i)).put("y",e.getY(i)));
    events.put(new JSONObject().put("action",e.getActionMasked()).put("count",e.getPointerCount()).put("source",e.getSource()).put("points",points));
    try(FileOutputStream out=openFileOutput("touch-receipt.json",MODE_PRIVATE)){out.write(new JSONObject().put("scope","Dedicated guest diagnostic surface, not video or optical latency").put("view_width",getWidth()).put("view_height",getHeight()).put("events",events).toString().getBytes("UTF-8"));}}
  }catch(Exception ignored){}return true;}
 });getWindow().getDecorView().post(()->{android.view.WindowInsetsController c=getWindow().getInsetsController();if(c!=null)c.hide(android.view.WindowInsets.Type.systemBars());});}
}
