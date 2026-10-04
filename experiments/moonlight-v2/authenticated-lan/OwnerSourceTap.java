package local.remoteandroid.direct;

import java.nio.charset.StandardCharsets;

/** Helper-only single native tap transaction; no credentials, files or network.
 * Hooks must recheck the captured Attempt/receiver/Surface on the UI thread in
 * the same critical section as dispatch. A local return is not remote playback.
 */
final class OwnerSourceTap {
    static final int DOWN=0,UP=1,CANCEL=3;
    interface Hooks {
        boolean owns() throws Exception;
        long uptimeMillis();
        void dispatch(int action,long downTime,float x,float y) throws Exception;
        void pause(long millis) throws Exception;
    }
    static final class Command {
        final int phase,x,y,width,height;final long nonce;
        Command(int phase,long nonce,int x,int y,int width,int height){
            this.phase=phase;this.nonce=nonce;this.x=x;this.y=y;this.width=width;this.height=height;
        }
        static Command parse(byte[] raw,int expectedPhase,long expectedNonce){
            if(raw==null||raw.length==0||raw.length>128||expectedNonce<=0
                    ||(expectedPhase!=1&&expectedPhase!=2))throw new IllegalArgumentException("source_command_bound");
            String text=new String(raw,StandardCharsets.US_ASCII);
            if(!text.matches("[1-2] [1-9][0-9]{0,18} [0-9]{1,5} [0-9]{1,5} [0-9]{1,4} [0-9]{1,4}\\n"))
                throw new IllegalArgumentException("source_command_schema");
            String[] fields=text.trim().split(" ");
            long nonce;int phase,x,y,w,h;
            try{phase=Integer.parseInt(fields[0]);nonce=Long.parseLong(fields[1]);
                x=Integer.parseInt(fields[2]);y=Integer.parseInt(fields[3]);w=Integer.parseInt(fields[4]);h=Integer.parseInt(fields[5]);}
            catch(NumberFormatException failure){throw new IllegalArgumentException("source_command_integer",failure);}
            if(phase!=expectedPhase||nonce!=expectedNonce||x<0||x>65535||y<0||y>65535
                    ||w<16||w>8192||h<16||h>8192)throw new IllegalArgumentException("source_command_binding");
            return new Command(phase,nonce,x,y,w,h);
        }
    }
    static final class Geometry {
        final int surfaceWidth,surfaceHeight,imageWidth,imageHeight;
        Geometry(int sw,int sh,int iw,int ih){
            if(sw<16||sh<16||iw<16||ih<16||sw>8192||sh>8192||iw>8192||ih>8192)
                throw new IllegalArgumentException("source_geometry_bound");
            surfaceWidth=sw;surfaceHeight=sh;imageWidth=iw;imageHeight=ih;
        }
        float[] point(Command command){
            if((long)command.width*imageHeight!=(long)command.height*imageWidth)
                throw new IllegalArgumentException("source_image_aspect_changed");
            float width=surfaceWidth,height=surfaceHeight;
            if((long)surfaceWidth*imageHeight>(long)surfaceHeight*imageWidth)width=(float)surfaceHeight*imageWidth/imageHeight;
            else height=(float)surfaceWidth*imageHeight/imageWidth;
            return new float[]{(surfaceWidth-width)/2+width*command.x/65535f,
                               (surfaceHeight-height)/2+height*command.y/65535f};
        }
    }
    static final class Receipt {
        boolean downAttempted,downReturned,upAttempted,upReturned;
        boolean cancelAttempted,cancelReturned,cancelSkippedForChangedOwner;
    }
    static void run(Hooks hooks,Command command,Geometry geometry,Receipt receipt)throws Exception{
        float[] point=geometry.point(command);long down=hooks.uptimeMillis();Throwable primary=null;
        try{
            if(!hooks.owns())throw new IllegalStateException("source_attempt_changed_before_down");
            receipt.downAttempted=true;hooks.dispatch(DOWN,down,point[0],point[1]);receipt.downReturned=true;
            hooks.pause(35); // Outside UI/Attempt/touch monitors; one tap, no retries.
            if(!hooks.owns())throw new IllegalStateException("source_attempt_changed_before_up");
            receipt.upAttempted=true;hooks.dispatch(UP,down,point[0],point[1]);receipt.upReturned=true;
        }catch(Throwable failure){primary=failure;throw failure;}
        finally{
            if(receipt.downAttempted&&!receipt.upReturned){
                try{
                    if(hooks.owns()){receipt.cancelAttempted=true;hooks.dispatch(CANCEL,down,point[0],point[1]);receipt.cancelReturned=true;}
                    else receipt.cancelSkippedForChangedOwner=true;
                }catch(Throwable cleanup){if(primary!=null)primary.addSuppressed(cleanup);else throw cleanup;}
            }
        }
    }
}
