package local.remoteandroid.direct;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Helper-only, non-input observation barrier inside one captured App Attempt.
 * No UI automation, snapshot, network, coordinate, key or cancellation operation.
 * The external observer has to qualify its own pixels and source separately.
 */
final class OwnerSourceFrame {
    interface Hooks {
        boolean owns() throws Exception;
        void published(byte[] nonce) throws Exception;
        byte[] confirmation() throws Exception;
    }
    static final class Receipt {
        boolean ownedBefore,publicationAttempted,publicationReturned;
        boolean confirmationMatched,ownedAfter;
    }
    static void observe(byte[] command,int phase,long nonce,Hooks hooks,Receipt receipt)throws Exception{
        if(command==null||command.length<1||command.length>32||phase!=1||nonce<=0)
            throw new IllegalArgumentException("source_frame_command_bound");
        byte[] expected=("READ "+nonce+"\n").getBytes(StandardCharsets.US_ASCII);
        if(!Arrays.equals(command,expected))throw new IllegalArgumentException("source_frame_command_binding");
        if(!hooks.owns())throw new IllegalStateException("source_frame_owner_changed_before");
        receipt.ownedBefore=true;
        byte[] body=(nonce+"\n").getBytes(StandardCharsets.US_ASCII);
        receipt.publicationAttempted=true;hooks.published(body.clone());receipt.publicationReturned=true;
        byte[] confirmation=hooks.confirmation();
        if(!Arrays.equals(confirmation,body))throw new IllegalStateException("source_frame_confirmation_changed");
        receipt.confirmationMatched=true;
        if(!hooks.owns())throw new IllegalStateException("source_frame_owner_changed_after");
        receipt.ownedAfter=true;
    }
}
