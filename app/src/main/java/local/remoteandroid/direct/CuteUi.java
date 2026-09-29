package local.remoteandroid.direct;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Outline;
import android.graphics.Typeface;
import android.graphics.drawable.*;
import android.provider.Settings;
import android.view.*;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.OvershootInterpolator;
import android.widget.*;

/** Native bounded ripples and brief press feedback; no animation loop during streaming. */
final class CuteUi {
    static final int INK=0xff285c62, MINT=0xffddf4ee, PINK=0xffffe2eb;
    static int dp(Context context,float value){return Math.round(value*context.getResources().getDisplayMetrics().density);}
    static GradientDrawable round(Context context,int color,int radius){
        GradientDrawable shape=new GradientDrawable();shape.setColor(color);shape.setCornerRadius(dp(context,radius));return shape;
    }
    static void style(Button button,int color,boolean compact){
        Context c=button.getContext();button.setAllCaps(false);button.setTextColor(color==PINK?0xffa44e70:INK);
        button.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));button.setTextSize(compact?12:15);
        button.setMinWidth(0);button.setMinimumWidth(0);button.setMinHeight(0);button.setMinimumHeight(0);
        button.setPadding(dp(c,10),0,dp(c,10),0);button.setStateListAnimator(null);button.setElevation(0);
        GradientDrawable fill=round(c,color,compact?16:22),mask=round(c,Color.WHITE,compact?16:22);
        RippleDrawable ripple=new RippleDrawable(ColorStateList.valueOf(color==PINK?0x40d66d97:0x4054bbaa),fill,mask);
        button.setBackground(new InsetDrawable(ripple,dp(c,4),dp(c,8),dp(c,4),dp(c,8)));
        button.setOnTouchListener((v,event)->{
            int action=event.getActionMasked();
            if(action==MotionEvent.ACTION_DOWN){
                v.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
                animate(v,.94f,.96f,80,false);
            }else if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_CANCEL)animate(v,1f,1f,180,true);
            return false;
        });
        button.setOnFocusChangeListener((v,focused)->{if(!focused){v.animate().cancel();v.setScaleX(1);v.setScaleY(1);}});
    }
    private static void animate(View view,float x,float y,int duration,boolean release){
        if(Settings.Global.getFloat(view.getContext().getContentResolver(),Settings.Global.ANIMATOR_DURATION_SCALE,1)==0){view.setScaleX(1);view.setScaleY(1);return;}
        view.animate().cancel();view.animate().scaleX(x).scaleY(y).setDuration(duration)
            .setInterpolator(release?new OvershootInterpolator(1.3f):new DecelerateInterpolator()).start();
    }
    static LinearLayout controls(Context context,java.util.function.IntConsumer click){
        LinearLayout bar=new LinearLayout(context);bar.setGravity(Gravity.CENTER);bar.setPadding(dp(context,8),0,dp(context,8),0);bar.setBackgroundColor(0xfff5fbf9);
        String[] labels={"返回","主页","任务","断开"};int[] icons={R.drawable.ic_back,R.drawable.ic_home,R.drawable.ic_tasks,R.drawable.ic_disconnect};
        for(int i=0;i<4;i++){
            final int action=i;Button button=new Button(context);button.setText(labels[i]);button.setContentDescription(labels[i]);
            style(button,i==3?PINK:MINT,true);Drawable icon=context.getDrawable(icons[i]);icon.setBounds(0,0,dp(context,16),dp(context,16));button.setCompoundDrawables(icon,null,null,null);button.setCompoundDrawablePadding(dp(context,4));
            bar.addView(button,new LinearLayout.LayoutParams(dp(context,76),dp(context,48)));
            button.setOnClickListener(v->click.accept(action));
        }
        return bar;
    }
    static ImageView avatar(Context context){
        ImageView image=new ImageView(context);image.setImageResource(R.drawable.avatar_photo);image.setScaleType(ImageView.ScaleType.CENTER_CROP);
        image.setContentDescription("给火锅的安卓头像");image.setClipToOutline(true);image.setOutlineProvider(new ViewOutlineProvider(){public void getOutline(View v,Outline o){o.setOval(0,0,v.getWidth(),v.getHeight());}});return image;
    }
}
