package com.commonwealthrobotics.kloster.tests.gui;

import com.commonwealthrobotics.input.TouchGestureGate;
import com.commonwealthrobotics.kloster.TouchInteractionModes;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.scene.Group;
import javafx.scene.Node;
import javafx.scene.Scene;
import javafx.scene.SubScene;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.PickResult;
import javafx.scene.input.TouchEvent;
import javafx.scene.input.TouchPoint;
import javafx.scene.input.ZoomEvent;
import javafx.scene.layout.Pane;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Stage-free probe of capture across a real SubScene event chain. */
public final class TouchModesProbe {
    private TouchModesProbe() { }

    public static void main(String[] args) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.startup(() -> {
            try { probe(); } catch (Throwable ex) { failure.set(ex); }
            finally { done.countDown(); }
        });
        if (!done.await(30, TimeUnit.SECONDS)) throw new AssertionError("touch modes timeout");
        Platform.exit();
        if (failure.get() != null) throw new AssertionError("touch modes failed", failure.get());
        System.out.println("TouchModesProbe: passed");
    }

    private static void probe() {
        Pane root = new Pane();
        Pane viewport = new Pane();
        root.getChildren().add(viewport);
        Group world = new Group();
        Group empty = new Group();
        empty.setId("kloster-selection-plane");
        Group model = new Group();
        world.getChildren().addAll(empty, model);
        viewport.getChildren().add(new SubScene(world, 300, 200));
        new Scene(root, 300, 200);
        Counts counts = new Counts();
        TouchInteractionModes.Router router = new TouchInteractionModes.Router(
                node -> node == model ? "shape" : null,
                node -> node == null || "kloster-selection-plane".equals(node.getId()),
                ignored -> counts.selected,
                ignored -> { counts.toggles++; counts.selected = !counts.selected; },
                ignored -> { counts.selects++; counts.selected = true; },
                () -> counts.clears++,
                (dx, dy, mode) -> {
                    counts.camera++;
                    if (mode == TouchInteractionModes.Mode.PAN) counts.pan++;
                }, () -> counts.aborts++);
        router.install(root, viewport);
        TouchGestureGate gate = new TouchGestureGate(() -> counts.pinchAborts++,
                new TouchGestureGate.ZoomSink() {
                    public void onTouchZoomFactor(double factor) { counts.zoom++; }
                    public void onNativeZoomFactor(double factor) { counts.zoom++; }
                });
        gate.install(viewport);
        model.addEventHandler(MouseEvent.MOUSE_PRESSED, event -> {
            counts.modelPress++;
            counts.selected = true;
        });
        model.addEventHandler(MouseEvent.MOUSE_DRAGGED, event -> counts.modelDrag++);
        model.addEventHandler(MouseEvent.MOUSE_CLICKED, event -> counts.modelClick++);
        empty.addEventHandler(MouseEvent.MOUSE_DRAGGED, event -> counts.emptyMouseDrag++);

        // A raw finger on the empty workplane rotates without starting its
        // synthesized selection-box mouse drag.
        touch(empty, TouchEvent.TOUCH_PRESSED, 1, point(1, TouchPoint.State.PRESSED, 10, 10, empty));
        mouse(empty, MouseEvent.MOUSE_PRESSED, 10, 10, true);
        touch(empty, TouchEvent.TOUCH_MOVED, 2, point(1, TouchPoint.State.MOVED, 30, 10, empty));
        mouse(empty, MouseEvent.MOUSE_DRAGGED, 30, 10, true);
        require(counts.camera == 1 && counts.emptyMouseDrag == 0, "empty one-finger orbit is isolated");
        touch(empty, TouchEvent.TOUCH_RELEASED, 3, point(1, TouchPoint.State.RELEASED, 30, 10, empty));
        mouse(empty, MouseEvent.MOUSE_RELEASED, 30, 10, true);
        mouse(empty, MouseEvent.MOUSE_CLICKED, 30, 10, true);

        // New raw touch clears the previous gesture's synthetic-mouse tail.
        touch(model, TouchEvent.TOUCH_PRESSED, 4, point(2, TouchPoint.State.PRESSED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        touch(model, TouchEvent.TOUCH_MOVED, 5, point(2, TouchPoint.State.MOVED, 80, 50, model));
        mouse(model, MouseEvent.MOUSE_DRAGGED, 80, 50, true);
        touch(model, TouchEvent.TOUCH_RELEASED, 6, point(2, TouchPoint.State.RELEASED, 80, 50, model));
        require(counts.modelPress == 1 && counts.modelDrag == 1 && counts.camera == 1,
                "object touch retains native selection and drag");

        router.setMode(TouchInteractionModes.Mode.MULTI);
        touch(model, TouchEvent.TOUCH_PRESSED, 100, point(99, TouchPoint.State.PRESSED, 60, 50, model));
        router.setMode(TouchInteractionModes.Mode.EDIT);
        mouse(model, MouseEvent.MOUSE_DRAGGED, 90, 50, true);
        touch(model, TouchEvent.TOUCH_RELEASED, 101, point(99, TouchPoint.State.RELEASED, 90, 50, model));
        mouse(model, MouseEvent.MOUSE_CLICKED, 90, 50, true);
        require(counts.modelDrag == 1 && counts.toggles == 0,
                "mode switch aborts the previous touch without leaking a model drag");
        router.setMode(TouchInteractionModes.Mode.MULTI);
        touch(model, TouchEvent.TOUCH_PRESSED, 7, point(3, TouchPoint.State.PRESSED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        touch(model, TouchEvent.TOUCH_RELEASED, 8, point(3, TouchPoint.State.RELEASED, 62, 50, model));
        mouse(model, MouseEvent.MOUSE_RELEASED, 62, 50, true);
        mouse(model, MouseEvent.MOUSE_CLICKED, 62, 50, true);
        require(counts.toggles == 1 && counts.modelPress == 1,
                "multi tap toggles without invoking model mover");
        touch(model, TouchEvent.TOUCH_PRESSED, 9, point(4, TouchPoint.State.PRESSED, 60, 50, model));
        touch(model, TouchEvent.TOUCH_MOVED, 10, point(4, TouchPoint.State.MOVED, 90, 50, model));
        touch(model, TouchEvent.TOUCH_RELEASED, 11, point(4, TouchPoint.State.RELEASED, 90, 50, model));
        require(counts.toggles == 1, "multi drag is not a tap");

        router.setMode(TouchInteractionModes.Mode.ORBIT);
        touch(model, TouchEvent.TOUCH_PRESSED, 12, point(5, TouchPoint.State.PRESSED, 60, 50, model));
        touch(model, TouchEvent.TOUCH_MOVED, 13, point(5, TouchPoint.State.MOVED, 80, 50, model));
        require(counts.camera == 2, "explicit orbit works over a model");
        touch(model, TouchEvent.TOUCH_PRESSED, 14,
                point(5, TouchPoint.State.STATIONARY, 80, 50, model),
                point(6, TouchPoint.State.PRESSED, 140, 50, empty));
        touch(model, TouchEvent.TOUCH_MOVED, 15,
                point(5, TouchPoint.State.MOVED, 100, 50, model),
                point(6, TouchPoint.State.MOVED, 160, 50, empty));
        require(counts.camera == 2 && counts.pinchAborts == 1,
                "second finger cancels camera drag and leaves pinch gate active");
        touch(model, TouchEvent.TOUCH_RELEASED, 16,
                point(5, TouchPoint.State.RELEASED, 100, 50, model),
                point(6, TouchPoint.State.RELEASED, 160, 50, empty));
        router.setMode(TouchInteractionModes.Mode.PAN);
        touch(model, TouchEvent.TOUCH_PRESSED, 17, point(7, TouchPoint.State.PRESSED, 60, 50, model));
        touch(model, TouchEvent.TOUCH_MOVED, 18, point(7, TouchPoint.State.MOVED, 80, 50, model));
        touch(model, TouchEvent.TOUCH_RELEASED, 19, point(7, TouchPoint.State.RELEASED, 80, 50, model));
        require(counts.pan == 1, "pan uses camera sink independently of model mover");

        router.setMode(TouchInteractionModes.Mode.EDIT);
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, false);
        require(counts.modelPress == 2, "ordinary mouse remains native in edit mode");
        mouse(empty, MouseEvent.MOUSE_PRESSED, 10, 10, true);
        mouse(empty, MouseEvent.MOUSE_DRAGGED, 30, 10, true);
        mouse(empty, MouseEvent.MOUSE_RELEASED, 30, 10, true);
        mouse(empty, MouseEvent.MOUSE_CLICKED, 30, 10, true);
        require(counts.camera == 4 && counts.emptyMouseDrag == 0,
                "synthesized-mouse-only empty drag rotates without selection-box drag");
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        require(counts.modelPress == 3,
                "synthesized-mouse-only figure press remains native editing");
        touch(world, TouchEvent.TOUCH_PRESSED, 20, point(8, TouchPoint.State.PRESSED, 200, 150, null));
        touch(world, TouchEvent.TOUCH_MOVED, 21, point(8, TouchPoint.State.MOVED, 220, 150, null));
        touch(world, TouchEvent.TOUCH_RELEASED, 22, point(8, TouchPoint.State.RELEASED, 220, 150, null));
        require(counts.camera == 5, "blank SubScene area with no picked node rotates view");
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, false);
        require(counts.modelPress == 4, "new physical mouse press immediately recovers after touch orbit");
        mouse(world, MouseEvent.MOUSE_CLICKED, 220, 150, true);
        mouse(empty, MouseEvent.MOUSE_PRESSED, 10, 10, true);
        mouse(empty, MouseEvent.MOUSE_DRAGGED, 30, 10, true);
        zoom(empty, ZoomEvent.ZOOM_STARTED);
        mouse(empty, MouseEvent.MOUSE_DRAGGED, 50, 10, true);
        zoom(empty, ZoomEvent.ZOOM_FINISHED);
        mouse(empty, MouseEvent.MOUSE_RELEASED, 50, 10, true);
        mouse(model, MouseEvent.MOUSE_CLICKED, 50, 10, true);
        require(counts.camera == 6 && counts.modelClick == 0,
                "native pinch cancels synthesized-mouse-only orbit before another drag");
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        require(counts.modelPress == 5,
                "next synthesized-only model tap recovers after native pinch tail");
        zoom(empty, ZoomEvent.ZOOM_FINISHED);
        mouse(model, MouseEvent.MOUSE_CLICKED, 60, 50, true);
        require(counts.modelClick == 1,
                "orphan native finish does not fabricate pinch ownership");
        mouse(model, MouseEvent.MOUSE_RELEASED, 60, 50, true);
        mouse(model, MouseEvent.MOUSE_CLICKED, 60, 50, true);

        // Native model selection happens on MOUSE_PRESSED. The router records
        // selection before that press, then applies an idempotent tap result.
        counts.selected = false;
        int togglesBeforeDefault = counts.toggles;
        rawModelTap(model, 30, 30);
        require(counts.selected && counts.toggles == togglesBeforeDefault,
                "first raw figure tap selects once despite native press selection: selected="
                        + counts.selected + " toggles=" + counts.toggles
                        + " before=" + togglesBeforeDefault + " selects=" + counts.selects);
        rawModelTap(model, 31, 33);
        require(!counts.selected && counts.toggles == togglesBeforeDefault + 1,
                "second raw figure tap deselects");
        rawModelTap(model, 32, 36);
        require(counts.selected && counts.toggles == togglesBeforeDefault + 1,
                "third raw figure tap selects again");
        touch(model, TouchEvent.TOUCH_PRESSED, 39, point(33, TouchPoint.State.PRESSED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        touch(model, TouchEvent.TOUCH_MOVED, 40, point(33, TouchPoint.State.MOVED, 90, 50, model));
        mouse(model, MouseEvent.MOUSE_DRAGGED, 90, 50, true);
        touch(model, TouchEvent.TOUCH_RELEASED, 41, point(33, TouchPoint.State.RELEASED, 90, 50, model));
        mouse(model, MouseEvent.MOUSE_RELEASED, 90, 50, true);
        require(counts.selected && counts.toggles == togglesBeforeDefault + 1,
                "raw figure drag never deselects");
        touch(model, TouchEvent.TOUCH_PRESSED, 44, point(34, TouchPoint.State.PRESSED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        mouse(model, MouseEvent.MOUSE_DRAGGED, 90, 50, true);
        touch(model, TouchEvent.TOUCH_RELEASED, 45, point(34, TouchPoint.State.RELEASED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_RELEASED, 90, 50, true);
        require(counts.selected && counts.toggles == togglesBeforeDefault + 1,
                "mouse drag also cancels tap when raw TOUCH_MOVED is absent");

        counts.selected = false;
        syntheticModelTap(model);
        require(counts.selected, "synthesized-mouse-only first figure tap selects");
        syntheticModelTap(model);
        require(!counts.selected, "synthesized-mouse-only second tap deselects");
        syntheticModelTap(model);
        require(counts.selected, "synthesized-mouse-only third tap selects");
        int togglesBeforeDrag = counts.toggles;
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        mouse(model, MouseEvent.MOUSE_DRAGGED, 90, 50, true);
        mouse(model, MouseEvent.MOUSE_RELEASED, 90, 50, true);
        require(counts.selected && counts.toggles == togglesBeforeDrag,
                "synthesized-mouse-only figure drag never deselects");
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, false);
        mouse(model, MouseEvent.MOUSE_RELEASED, 60, 50, false);
        require(counts.selected && counts.toggles == togglesBeforeDrag,
                "ordinary mouse click retains its native selection behavior");

        counts.selected = false;
        touch(model, TouchEvent.TOUCH_PRESSED, 42, point(40, TouchPoint.State.PRESSED, 60, 50, model));
        touch(model, TouchEvent.TOUCH_RELEASED, 43, point(40, TouchPoint.State.RELEASED, 60, 50, model));
        require(counts.selected, "raw-touch-only driver can select without a mouse press");
    }

    private static void rawModelTap(Node model, int finger, int setBase) {
        touch(model, TouchEvent.TOUCH_PRESSED, setBase,
                point(finger, TouchPoint.State.PRESSED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        touch(model, TouchEvent.TOUCH_RELEASED, setBase + 1,
                point(finger, TouchPoint.State.RELEASED, 60, 50, model));
        mouse(model, MouseEvent.MOUSE_RELEASED, 60, 50, true);
        mouse(model, MouseEvent.MOUSE_CLICKED, 60, 50, true);
    }

    private static void syntheticModelTap(Node model) {
        mouse(model, MouseEvent.MOUSE_PRESSED, 60, 50, true);
        mouse(model, MouseEvent.MOUSE_RELEASED, 60, 50, true);
        mouse(model, MouseEvent.MOUSE_CLICKED, 60, 50, true);
    }

    private static TouchPoint point(int id, TouchPoint.State state, double x, double y, Node pick) {
        return new TouchPoint(id, state, x, y, x, y, pick, new PickResult(pick, x, y));
    }

    private static void touch(Node target, javafx.event.EventType<TouchEvent> type,
            int setId, TouchPoint... points) {
        Event.fireEvent(target, new TouchEvent(target, target, type, points[0], List.of(points), setId,
                false, false, false, false));
    }

    private static void mouse(Node target, javafx.event.EventType<MouseEvent> type,
            double x, double y, boolean synthesized) {
        Event.fireEvent(target, new MouseEvent(target, target, type, x, y, x, y, MouseButton.PRIMARY, 1,
                false, false, false, false, false, false, false, false, false, synthesized,
                false, true, new PickResult(target, x, y)));
    }

    private static void zoom(Node target, javafx.event.EventType<ZoomEvent> type) {
        Event.fireEvent(target, new ZoomEvent(target, target, type, 10, 10, 10, 10,
                false, false, false, false, true, false, 1.0, 1.0, null));
    }

    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    private static final class Counts {
        int toggles, clears, camera, pan, aborts, pinchAborts, zoom;
        int modelPress, modelDrag, modelClick, emptyMouseDrag;
        int selects;
        boolean selected;
    }
}
