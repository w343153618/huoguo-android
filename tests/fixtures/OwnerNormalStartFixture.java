package local.remoteandroid.direct;
import java.util.concurrent.CountDownLatch;
public final class OwnerNormalStartFixture {
 static void ok(boolean b){InputDrainFixture.check(b);}static void refuse(Runnable r){InputDrainFixture.refuse(r);}
 public static void main(String[] args)throws Exception{
  String mode=args[0];AuthenticatedLanUdpUi ui=new AuthenticatedLanUdpUi(new InputQueue(new InputDrainFixture.Manual()));
  ui.address.text=AuthenticatedLanUdpUi.DEFAULT_LAN_ADDRESS;ui.user.text="huoguo";ui.password.text="synthetic_public_manual_value";
  ui.ownerNormalStartButton.action=ui::fixtureNormalClick;long end=System.nanoTime()+20_000_000_000L;
  try{
   switch(mode){
    case "normal":case "factory_blocks":
     if(mode.equals("factory_blocks"))ui.ownerNormalStartButton.action=()->{ui.fixtureNormalClick();InputDrainFixture.await(ui.authTrying);ok(ui.current.receiver==null && ui.authThread.isAlive());};
     AuthenticatedLanUdpUi.OwnerStartTransaction value=ui.ownerNormalStartRendezvous(end);
     ui.authThread.join();ok(ui.authFailure==null);InputDrainFixture.await(ui.actualReceiver.entered);
     ok(ui.current.ownerRendezvous==value.preparedRendezvous() && ui.passwordStore.loads==1
       && !value.operatorQualified && !value.phoneQualified && !value.releaseEligible);break;
    case "ordinary":ui.fixtureNormalClick();ui.authThread.join();ok(ui.ownerStartTransaction==null && ui.current.ownerRendezvous==null);break;
    case "expired":refuse(()->ui.ownerNormalStartRendezvous(System.nanoTime()-1));ok(ui.passwordStore.loads==0);break;
    case "oversize":refuse(()->ui.ownerNormalStartRendezvous(System.nanoTime()+31_000_000_000L));break;
    case "lock_wait":
     CountDownLatch held=new CountDownLatch(1);Thread t=new Thread(()->{synchronized(ui.lock){held.countDown();try{Thread.sleep(3100);}catch(InterruptedException e){Thread.currentThread().interrupt();}}});
     t.start();InputDrainFixture.await(held);refuse(()->ui.ownerNormalStartRendezvous(end));t.join();ok(ui.ownerStartTransaction==null && ui.passwordStore.loads==0);break;
    case "nonmain":android.os.Looper.myLooper();Thread f=new Thread(()->refuse(()->ui.ownerNormalStartRendezvous(end)));f.start();f.join();ok(ui.passwordStore.loads==0);break;
    case "repeat":ui.ownerNormalStartRendezvous(end);refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "active":ui.current=new AuthenticatedLanUdpUi.Attempt(7,"","","lan","",null,false,0,false,false);refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "retiring":ui.retiring=new AuthenticatedLanUdpUi.Attempt(7,"","","lan","",null,false,0,false,false);refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "destroyed":ui.activity.dead=true;refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "scope":ui.scope.selected=2;refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "route":ui.address.text="public.invalid:45560";refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "account":ui.user.text="foreign";refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "detached":ui.ownerNormalStartButton.attached=false;refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "disabled":ui.ownerNormalStartButton.enabled=false;refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "restore_empty":ui.passwordStore.bound="public.invalid:45560";refuse(()->ui.ownerNormalStartRendezvous(end));ok(ui.password.getText().isEmpty() && ui.current==null);break;
    case "restore_failure":ui.passwordStore.fail=true;refuse(()->ui.ownerNormalStartRendezvous(end));ok(ui.password.getText().isEmpty() && ui.current==null);break;
    case "restore_replaced_secret":ui.password.changed=()->ui.password.text="foreign_nonempty";refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "restore_reentrant":ui.password.changed=()->refuse(()->ui.ownerNormalStartRendezvous(end));refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "button_reentrant":ui.ownerNormalStartButton.action=()->{refuse(()->ui.ownerNormalStartRendezvous(end));ui.fixtureNormalClick();};refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "click_refused":ui.ownerNormalStartButton.result=false;refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "click_no_attempt":ui.ownerNormalStartButton.action=()->{};refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "click_throws":ui.ownerNormalStartButton.action=()->{ui.fixtureNormalClick();throw new IllegalStateException("synthetic callback failure");};refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "click_deadline":ui.ownerNormalStartButton.action=()->{ui.fixtureNormalClick();try{Thread.sleep(3100);}catch(InterruptedException e){Thread.currentThread().interrupt();}};refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "button_read_timeout":ui.ownerNormalStartButton.attachedRead=()->{try{Thread.sleep(3100);}catch(InterruptedException e){Thread.currentThread().interrupt();}};refuse(()->ui.ownerNormalStartRendezvous(end));ok(ui.passwordStore.loads==0);break;
    case "later_page":ui.password.changed=()->ui.loginRevision++;refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "later_attempt":ui.ownerNormalStartButton.action=()->{ui.fixtureNormalClick();ui.current=new AuthenticatedLanUdpUi.Attempt(8,AuthenticatedLanUdpUi.DEFAULT_LAN_ADDRESS,"","lan","",null,false,0,false,false);};refuse(()->ui.ownerNormalStartRendezvous(end));break;
    case "after_unknown":ui.passwordStore.fail=true;refuse(()->ui.ownerNormalStartRendezvous(end));refuse(()->ui.ownerStartTransaction.preparedRendezvous());break;
    default:throw new AssertionError(mode);
   }
  }finally{
   if(ui.authThread!=null)ui.authThread.join();
   UdpVideoProbe receiver=ui.actualReceiver;if(receiver==null && ui.current!=null)receiver=ui.current.receiver;
   if(receiver!=null){InputDrainFixture.await(receiver.entered);receiver.permit.countDown();receiver.actualThread.join();}
  }
  System.out.println("PASS actual normal creation/restore/factory transaction; authentication/Android synthetic; permission false");
 }
}
