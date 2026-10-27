package com.commonwealthrobotics.input;

import javafx.scene.Node;
import javafx.scene.Scene;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.ScrollEvent;
import javafx.scene.input.TouchEvent;
import javafx.scene.input.TouchPoint;
import javafx.scene.input.ZoomEvent;

import java.util.List;
import java.util.Objects;

/** Filters direct-touch and native zoom input before viewport child handlers. */
public final class TouchGestureGate {
    public interface ZoomSink {
        void onTouchZoomFactor(double factor);
        void onNativeZoomFactor(double factor);
    }

    private final Runnable abortEntireGesture;
    private final ZoomSink zoomSink;
    private boolean physicalPinching;
    private boolean nativePinching;
    private boolean suppressDirectZoomTail;
    private boolean suppressSyntheticMouse;
    private boolean haveDistance;
    private double lastDistance;
    private int pairA = Integer.MIN_VALUE;
    private int pairB = Integer.MIN_VALUE;
    private int lastMoveEventSet = Integer.MIN_VALUE;
    private int activeTouches;

    public TouchGestureGate(Runnable abortEntireGesture, ZoomSink zoomSink) {
        this.abortEntireGesture = Objects.requireNonNull(abortEntireGesture);
        this.zoomSink = Objects.requireNonNull(zoomSink);
    }

    public void install(Scene scene) {
        Objects.requireNonNull(scene).addEventFilter(TouchEvent.ANY, this::handleTouch);
        scene.addEventFilter(MouseEvent.ANY, this::handleMouse);
        scene.addEventFilter(ZoomEvent.ANY, this::handleZoom);
        scene.addEventFilter(ScrollEvent.ANY, this::handleScroll);
    }

    public void install(Node viewport) {
        Objects.requireNonNull(viewport).addEventFilter(TouchEvent.ANY, this::handleTouch);
        viewport.addEventFilter(MouseEvent.ANY, this::handleMouse);
        viewport.addEventFilter(ZoomEvent.ANY, this::handleZoom);
        viewport.addEventFilter(ScrollEvent.ANY, this::handleScroll);
    }

    public boolean isPinching() { return physicalPinching || nativePinching; }
    public int getActiveTouches() { return activeTouches; }

    public void handleTouch(TouchEvent event) {
        List<TouchPoint> points = event.getTouchPoints();
        int live = countLive(points);
        boolean firstFinger = event.getEventType() == TouchEvent.TOUCH_PRESSED && activeTouches == 0;
        if (firstFinger) {
            suppressSyntheticMouse = false;
            suppressDirectZoomTail = false;
            physicalPinching = false;
            nativePinching = false;
            haveDistance = false;
            pairA = pairB = Integer.MIN_VALUE;
            lastMoveEventSet = Integer.MIN_VALUE;
        }
        activeTouches = live;

        if (event.getEventType() == TouchEvent.TOUCH_PRESSED && live >= 2 && !physicalPinching) {
            physicalPinching = true;
            suppressDirectZoomTail = true;
            suppressSyntheticMouse = true;
            selectPair(points);
            resetDistance(points);
            abortEntireGesture.run();
            event.consume();
            return;
        }

        if (physicalPinching) {
            if (live < 2) {
                physicalPinching = false;
                haveDistance = false;
                pairA = pairB = Integer.MIN_VALUE;
            } else if (event.getEventType() == TouchEvent.TOUCH_MOVED
                    && event.getEventSetId() != lastMoveEventSet) {
                lastMoveEventSet = event.getEventSetId();
                if (!samePair(points)) {
                    selectPair(points);
                    resetDistance(points);
                } else {
                    double current = distance(points, pairA, pairB);
                    if (haveDistance && lastDistance > 0.0 && current > 0.0) {
                        zoomSink.onTouchZoomFactor(current / lastDistance);
                    }
                    lastDistance = current;
                    haveDistance = current > 0.0;
                }
            }
            event.consume();
        }
    }

    public void handleMouse(MouseEvent event) {
        // A few touchscreen drivers mark their touch-generated mouse events as
        // ordinary mouse events. Never let those events reach selection or
        // resize handlers while either pinch path owns the gesture.
        if (physicalPinching || nativePinching) {
            event.consume();
            return;
        }
        if (!event.isSynthesized()) {
            if (event.getEventType() == MouseEvent.MOUSE_PRESSED) {
                suppressSyntheticMouse = false;
                suppressDirectZoomTail = false;
            }
            return;
        }
        if (physicalPinching || nativePinching || suppressSyntheticMouse) {
            event.consume();
            if (event.getEventType() == MouseEvent.MOUSE_CLICKED) {
                suppressSyntheticMouse = false;
                nativePinching = false;
            }
        }
    }

    public void handleZoom(ZoomEvent event) {
        // Some drivers deliver their native pinch after the last raw touch release.
        // It describes the same gesture, so applying it would zoom twice.
        if (activeTouches > 0 || physicalPinching || (suppressDirectZoomTail && event.isDirect())) {
            if (event.getEventType() == ZoomEvent.ZOOM_FINISHED && activeTouches == 0)
                suppressDirectZoomTail = false;
            event.consume();
            return;
        }
        if (event.getEventType() == ZoomEvent.ZOOM_STARTED) {
            if (!nativePinching) {
                nativePinching = true;
                suppressSyntheticMouse = true;
                abortEntireGesture.run();
            }
            event.consume();
        } else if (event.getEventType() == ZoomEvent.ZOOM) {
            if (!nativePinching) {
                nativePinching = true;
                suppressSyntheticMouse = true;
                abortEntireGesture.run();
            }
            zoomSink.onNativeZoomFactor(event.getZoomFactor());
            event.consume();
        } else if (event.getEventType() == ZoomEvent.ZOOM_FINISHED) {
            nativePinching = false;
            event.consume();
        }
    }

    public void handleScroll(ScrollEvent event) {
        if (activeTouches > 0 || physicalPinching || nativePinching
                || (suppressDirectZoomTail && (event.isDirect() || event.isInertia()))) event.consume();
    }

    private void selectPair(List<TouchPoint> points) {
        int first = Integer.MAX_VALUE;
        int second = Integer.MAX_VALUE;
        for (TouchPoint point : points) {
            if (point.getState() == TouchPoint.State.RELEASED) continue;
            int id = point.getId();
            if (id < first) { second = first; first = id; }
            else if (id < second) second = id;
        }
        pairA = first == Integer.MAX_VALUE ? Integer.MIN_VALUE : first;
        pairB = second == Integer.MAX_VALUE ? Integer.MIN_VALUE : second;
    }

    private boolean samePair(List<TouchPoint> points) {
        boolean first = false, second = false;
        for (TouchPoint point : points) {
            if (point.getState() == TouchPoint.State.RELEASED) continue;
            if (point.getId() == pairA) first = true;
            if (point.getId() == pairB) second = true;
        }
        return first && second;
    }

    private void resetDistance(List<TouchPoint> points) {
        lastDistance = distance(points, pairA, pairB);
        haveDistance = lastDistance > 0.0;
    }

    private static int countLive(List<TouchPoint> points) {
        int result = 0;
        for (TouchPoint point : points)
            if (point.getState() != TouchPoint.State.RELEASED) result++;
        return result;
    }

    private static double distance(List<TouchPoint> points, int a, int b) {
        TouchPoint first = null, second = null;
        for (TouchPoint point : points) {
            if (point.getState() == TouchPoint.State.RELEASED) continue;
            if (point.getId() == a) first = point;
            else if (point.getId() == b) second = point;
        }
        return first == null || second == null ? 0.0
                : Math.hypot(second.getSceneX() - first.getSceneX(), second.getSceneY() - first.getSceneY());
    }
}
