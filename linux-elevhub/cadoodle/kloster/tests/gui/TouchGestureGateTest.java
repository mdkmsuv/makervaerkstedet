package com.commonwealthrobotics.kloster.tests.gui;

import com.commonwealthrobotics.input.TouchGestureGate;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.scene.Group;
import javafx.scene.Node;
import javafx.scene.Scene;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.PickResult;
import javafx.scene.input.ScrollEvent;
import javafx.scene.input.TouchEvent;
import javafx.scene.input.TouchPoint;
import javafx.scene.input.ZoomEvent;

import java.util.Arrays;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Stage-free FX probe for touch/mouse/zoom arbitration. */
public final class TouchGestureGateTest {
    private TouchGestureGateTest() { }

    public static void main(String[] args) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.startup(() -> { try { runProbe(); } catch (Throwable ex) { failure.set(ex); } finally { done.countDown(); } });
        if (!done.await(30, TimeUnit.SECONDS)) throw new AssertionError("touch gate timeout");
        Platform.exit();
        if (failure.get() != null) throw new AssertionError("touch gate failed", failure.get());
        System.out.println("TouchGestureGateTest: passed");
    }

    private static void runProbe() {
        Group viewport = new Group();
        Group child = new Group();
        viewport.getChildren().add(child);
        Scene scene = new Scene(viewport, 320, 240);
        Counters c = new Counters();
        TouchGestureGate gate = new TouchGestureGate(() -> c.aborts++, new TouchGestureGate.ZoomSink() {
            @Override public void onTouchZoomFactor(double factor) { c.touchFactors++; c.touchProduct *= factor; }
            @Override public void onNativeZoomFactor(double factor) { c.nativeFactors++; c.nativeProduct *= factor; }
        });
        gate.install(viewport);
        scene.addEventHandler(MouseEvent.ANY, e -> { if (!e.isConsumed()) c.mouse++; });
        scene.addEventHandler(ScrollEvent.ANY, e -> { if (!e.isConsumed()) c.scroll++; });

        fireTouch(child, TouchEvent.TOUCH_PRESSED, 1, point(1, TouchPoint.State.PRESSED, 80, 100));
        fireMouse(child, MouseEvent.MOUSE_PRESSED, true);
        fireTouch(child, TouchEvent.TOUCH_MOVED, 2, point(1, TouchPoint.State.MOVED, 84, 100));
        fireMouse(child, MouseEvent.MOUSE_DRAGGED, true);
        require(c.aborts == 0 && c.mouse == 2, "one-finger synthesized mouse path");

        fireTouch(child, TouchEvent.TOUCH_PRESSED, 3,
                point(1, TouchPoint.State.STATIONARY, 84, 100), point(2, TouchPoint.State.PRESSED, 184, 100));
        require(c.aborts == 1 && gate.isPinching(), "second finger abort");
        // Some Windows touch drivers report the touch mouse stream as ordinary
        // mouse events. It must still be consumed while the raw pinch owns it.
        int mouseBeforePinch = c.mouse;
        fireMouse(child, MouseEvent.MOUSE_DRAGGED, false);
        require(c.mouse == mouseBeforePinch, "ordinary mouse stream must be suppressed during raw pinch");
        fireTouch(child, TouchEvent.TOUCH_MOVED, 4,
                point(1, TouchPoint.State.MOVED, 74, 100), point(2, TouchPoint.State.MOVED, 194, 100));
        fireTouch(child, TouchEvent.TOUCH_MOVED, 4,
                point(1, TouchPoint.State.MOVED, 74, 100), point(2, TouchPoint.State.MOVED, 194, 100));
        require(c.touchFactors == 1 && Math.abs(c.touchProduct - 1.2) < 1e-6, "factor and event-set deduplication");
        fireZoom(child, ZoomEvent.ZOOM, 1.3);
        require(c.nativeFactors == 0, "native zoom suppressed during touch");

        fireTouch(child, TouchEvent.TOUCH_RELEASED, 5,
                point(1, TouchPoint.State.RELEASED, 74, 100), point(2, TouchPoint.State.STATIONARY, 194, 100));
        fireTouch(child, TouchEvent.TOUCH_RELEASED, 6,
                point(1, TouchPoint.State.RELEASED, 74, 100), point(2, TouchPoint.State.RELEASED, 194, 100));
        require(!gate.isPinching(), "physical pinch ends without synthetic click");
        fireScroll(child, ScrollEvent.SCROLL, true, false, 40);
        fireScroll(child, ScrollEvent.SCROLL, true, true, 40);
        require(c.scroll == 0, "direct and inertia scroll tails must be suppressed after touch pinch");
        fireZoom(child, ZoomEvent.ZOOM_STARTED, 1.0);
        fireZoom(child, ZoomEvent.ZOOM, 1.3);
        fireZoom(child, ZoomEvent.ZOOM_FINISHED, 1.0);
        require(c.nativeFactors == 0 && c.aborts == 1,
                "late native zoom after raw touch release must not zoom twice");
        fireMouse(child, MouseEvent.MOUSE_CLICKED, true);
        // A driver may emit a native finish without a start, or a scale event
        // without a start. Both sequences must drain without leaking to the
        // viewport and a later ordinary mouse drag must still work.
        fireZoom(child, ZoomEvent.ZOOM_FINISHED, 1.0);
        fireZoom(child, ZoomEvent.ZOOM, 1.2);
        require(c.nativeFactors == 1 && c.aborts == 2,
                "native zoom edge lifecycle must abort once and apply one factor");
        fireZoom(child, ZoomEvent.ZOOM_FINISHED, 1.0);
        fireMouse(child, MouseEvent.MOUSE_DRAGGED, false);
        require(c.mouse == 3, "ordinary non-synthesized drag must recover after native zoom tail");
        fireTouch(child, TouchEvent.TOUCH_PRESSED, 7, point(3, TouchPoint.State.PRESSED, 50, 50));
        fireMouse(child, MouseEvent.MOUSE_PRESSED, true);
        require(c.mouse == 4, "tail drained on next first finger");

        fireTouch(child, TouchEvent.TOUCH_PRESSED, 8,
                point(3, TouchPoint.State.STATIONARY, 50, 50), point(4, TouchPoint.State.PRESSED, 100, 50));
        fireTouch(child, TouchEvent.TOUCH_MOVED, 9,
                point(3, TouchPoint.State.MOVED, 40, 50), point(4, TouchPoint.State.MOVED, 110, 50));
        fireTouch(child, TouchEvent.TOUCH_PRESSED, 10,
                point(3, TouchPoint.State.STATIONARY, 40, 50), point(5, TouchPoint.State.PRESSED, 140, 50));
        fireTouch(child, TouchEvent.TOUCH_MOVED, 11,
                point(3, TouchPoint.State.MOVED, 40, 50), point(5, TouchPoint.State.MOVED, 160, 50));
        require(c.touchFactors == 2, "pair replacement resets baseline without jump");

        fireTouch(child, TouchEvent.TOUCH_RELEASED, 12,
                point(3, TouchPoint.State.RELEASED, 40, 50), point(5, TouchPoint.State.RELEASED, 160, 50));
        // A raw-touch-only driver may never send ZOOM_FINISHED. A fresh touch
        // must still work and begin a separate gesture.
        fireTouch(child, TouchEvent.TOUCH_PRESSED, 13, point(6, TouchPoint.State.PRESSED, 50, 50));
        fireMouse(child, MouseEvent.MOUSE_PRESSED, true);
        require(c.mouse == 5, "new touch recovers when no native zoom finish was sent");
        fireTouch(child, TouchEvent.TOUCH_RELEASED, 14, point(6, TouchPoint.State.RELEASED, 50, 50));
        fireZoom(child, ZoomEvent.ZOOM_STARTED, 1.0);
        fireZoom(child, ZoomEvent.ZOOM, 1.1);
        fireZoom(child, ZoomEvent.ZOOM_FINISHED, 1.0);
        require(c.aborts == 4 && c.nativeFactors == 2 && Math.abs(c.nativeProduct - 1.32) < 1e-6,
                "native zoom lifecycle and abort");
        fireMouse(child, MouseEvent.MOUSE_RELEASED, true);
        fireMouse(child, MouseEvent.MOUSE_CLICKED, true);
        fireMouse(child, MouseEvent.MOUSE_PRESSED, false);
        require(c.mouse == 6, "ordinary mouse after native zoom");
    }

    private static TouchPoint point(int id, TouchPoint.State state, double x, double y) {
        return new TouchPoint(id, state, x, y, x, y, null, new PickResult(null, x, y));
    }
    private static void fireTouch(Node target, javafx.event.EventType<TouchEvent> type, int setId, TouchPoint... points) {
        List<TouchPoint> all = Arrays.asList(points);
        Event.fireEvent(target, new TouchEvent(target, target, type, points[0], all, setId,
                false, false, false, false));
    }
    private static void fireMouse(Node target, javafx.event.EventType<MouseEvent> type, boolean synthesized) {
        Event.fireEvent(target, new MouseEvent(target, target, type, 10, 10, 10, 10, MouseButton.PRIMARY, 1,
                false, false, false, false, false, false, false, false, false, synthesized, false, true,
                new PickResult(target, 10, 10)));
    }
    private static void fireZoom(Node target, javafx.event.EventType<ZoomEvent> type, double factor) {
        Event.fireEvent(target, new ZoomEvent(target, target, type, 10, 10, 10, 10,
                false, false, false, false, true, false, factor, factor, null));
    }
    private static void fireScroll(Node target, javafx.event.EventType<ScrollEvent> type,
                                   boolean direct, boolean inertia, double deltaY) {
        Event.fireEvent(target, new ScrollEvent(target, target, type, 10, 10, 10, 10,
                false, false, false, false, direct, inertia,
                0, deltaY, 0, deltaY, null, 0, null, 0, 0, null));
    }
    private static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
    private static final class Counters {
        int aborts, mouse, touchFactors, nativeFactors, scroll;
        double touchProduct = 1.0, nativeProduct = 1.0;
    }
}
