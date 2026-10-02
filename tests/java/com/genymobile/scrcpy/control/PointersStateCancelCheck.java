package com.genymobile.scrcpy.control;

import android.view.MotionEvent;
import com.genymobile.scrcpy.model.Point;
import java.util.HashSet;

/** Exercises the real guest PointersState with data-only Android structs offline. */
public final class PointersStateCancelCheck {
    private static int checks;
    private static void check(boolean value) {
        checks++;
        if (!value) {
            throw new AssertionError("Guest pointer cancellation check " + checks);
        }
    }

    public static void main(String[] args) {
        PointersState state = new PointersState();
        MotionEvent.PointerProperties[] props = new MotionEvent.PointerProperties[10];
        MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[10];
        for (int i = 0; i < 10; i++) {
            props[i] = new MotionEvent.PointerProperties();
            coords[i] = new MotionEvent.PointerCoords();
            int index = state.getPointerIndex(101 + i);
            check(index == i);
            Pointer pointer = state.get(index);
            pointer.setPoint(new Point(i * 17, i * 31));
            pointer.setPressure(i / 10f);
        }
        check(state.getPointerIndex(1000) == -1);
        check(state.cancel(props, coords) == 10);
        HashSet<Integer> localIds = new HashSet<>();
        for (int i = 0; i < 10; i++) {
            check(localIds.add(props[i].id));
            check(props[i].id == i);
            check(coords[i].x == i * 17 && coords[i].y == i * 31);
            check(coords[i].pressure == i / 10f);
        }
        check(state.cancel(props, coords) == 0); // Duplicate must not allocate a phantom contact.
        int next = state.getPointerIndex(1001);
        check(next == 0);
        check(state.get(next).getLocalId() == 0);
        state.get(next).setPoint(new Point(719, 1279));
        state.get(next).setPressure(1);
        check(state.cancel(props, coords) == 1);
        check(props[0].id == 0 && coords[0].x == 719 && coords[0].y == 1279);
        check(state.cancel(props, coords) == 0);
        // Reusing an external ID after cancel creates a genuinely fresh guest pointer.
        check(state.getPointerIndex(101) == 0);
        check(state.get(0).getLocalId() == 0);
        state.get(0).setPoint(new Point(0, 0));
        state.get(0).setPressure(.5f);
        state.get(0).setUp(true);
        check(state.update(props, coords) == 1); // Existing UP semantics are preserved.
        check(state.cancel(props, coords) == 0);
        for (int i = 0; i < 10; i++) {
            check(state.getPointerIndex(200 + i) == i);
            state.get(i).setPoint(new Point(i, i));
            state.get(i).setPressure(.25f);
        }
        check(state.cancel(props, coords) == 10);
        check(state.cancel(props, coords) == 0);
        System.out.println("PASS " + checks + " guest pointer cancel bookkeeping checks (offline; no Android injection)");
    }
}
