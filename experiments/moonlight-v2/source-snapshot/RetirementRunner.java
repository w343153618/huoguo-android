package local.huoguo.sourceprobe;

import android.os.Bundle;
import com.android.uiautomator.testrunner.UiAutomatorTestRunner;
import java.util.List;

/** Explicit pinned legacy framework runner; does not alter framework cleanup. */
@SuppressWarnings("deprecation")
public final class RetirementRunner extends UiAutomatorTestRunner {
    private RetirementReceipt receipt;

    @Override public void run(List<String> classes, Bundle params, boolean debug,
                              boolean monkey) {
        if (receipt != null || classes == null || classes.size() != 1
                || !"local.huoguo.sourceprobe.SourceSnapshot#testSnapshot".equals(classes.get(0))
                || params == null || debug || monkey) {
            throw new IllegalArgumentException("retirement_run_rejected");
        }
        receipt = RetirementReceipt.prepare(params.getString("relative"));
        // Installed run dispatches start virtually, then System.exit(0).
        super.run(classes, params, debug, monkey);
        throw new IllegalStateException("framework_run_unexpected_return");
    }

    @Override protected void start() {
        if (receipt == null) throw new IllegalStateException("retirement_not_prepared");
        super.start();
        // No finally: failures/uncaught exceptions cannot create this receipt.
        // Normal return follows installed disconnect()/quit() call sites.
        receipt.normalStartReturned();
    }
}
