package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine;
import com.neuronrobotics.sdk.addons.kinematics.math.RotationNR;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import javafx.scene.Node;
import javafx.scene.SubScene;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.ToggleGroup;
import javafx.scene.control.Tooltip;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.PickResult;
import javafx.scene.input.TouchEvent;
import javafx.scene.input.TouchPoint;
import javafx.scene.input.ZoomEvent;
import javafx.scene.layout.HBox;
import javafx.scene.layout.Pane;

import java.util.Objects;
import java.util.function.Consumer;
import java.util.function.Function;
import java.util.function.Predicate;

/** Direct-touch routing. A camera gesture never reaches a model's mouse mover. */
public final class TouchInteractionModes {
    private TouchInteractionModes() { }

    public enum Mode { EDIT, MULTI, ORBIT, PAN }

    public static void install(ActiveProject ap, SelectionSession session,
            BowlerStudio3dEngine engine, Pane root) {
        Objects.requireNonNull(ap);
        Objects.requireNonNull(session);
        Objects.requireNonNull(engine);
        Objects.requireNonNull(root);
        Node viewport = root.lookup("#view3d");
        HBox tools = (HBox) root.lookup("#kloster-touch-tools");
        if (viewport == null || tools == null)
            throw new IllegalStateException("CaDoodle touch viewport or toolbar is missing");

        Router router = new Router(node -> {
            for (var entry : session.getMeshes().entrySet())
                if (entry.getValue() != null && entry.getValue().display == node)
                    return entry.getKey();
            return null;
        }, node -> {
            if (session.workplane.isActive()) return false;
            if (node == null) return true;
            if (node == viewport || node instanceof SubScene || engine.isSubScene(node)
                    || "kloster-selection-plane".equals(node.getId()))
                return true;
            if (engine.getWorkplaneGroup() != null
                    && node == engine.getWorkplaneGroup().intersectionNode) return true;
            return session.workplane.getPlacementPlane() != null
                    && node == session.workplane.getPlacementPlane().intersectionNode;
        }, model -> session.selectedSnapshot().contains(((CSG) model).getName()), model -> {
            if (ap.get() != null && !ModelEditQueue.isBusy(ap) && !ap.get().isOperationRunning())
                session.toggleTouchSelection((CSG) model);
        }, model -> {
            if (ap.get() != null && !ModelEditQueue.isBusy(ap) && !ap.get().isOperationRunning())
                session.selectOnlyTouchModel((CSG) model);
        }, () -> {
            if (ap.get() != null && !ModelEditQueue.isBusy(ap) && !ap.get().isOperationRunning())
                session.clearSelection();
        }, (dx, dy, mode) -> {
            if (mode == Mode.PAN) {
                double depth = Math.min(100, -100.0 / engine.getFlyingCamera().getZoomDepth());
                if (Math.abs(depth) > 1e-6)
                    engine.moveCamera(new TransformNR(dx * .05 / depth, dy * .05 / depth, 0,
                            new RotationNR()));
            } else {
                double side = engine.getFlyingCamera().getTiltAngle() > 0 ? -1 : 1;
                engine.moveCamera(new TransformNR(0, 0, 0,
                        new RotationNR(dy * .18, side * dx * .18, 0)));
            }
        }, session::abortInteraction);

        // The parent captures first. The existing pinch gate remains on view3d,
        // and still receives every raw TouchEvent and ZoomEvent.
        router.install(root, viewport);

        populateControls(tools, router);
    }

    static void populateControls(HBox tools, Router router) {
        ToggleGroup group = new ToggleGroup();
        ToggleButton edit = button("Byg", "kloster-touch-edit",
                "Tryk på en figur for at vælge eller fravælge den. Træk på tom plade for at dreje.", Mode.EDIT, router, group);
        ToggleButton multi = button("Vælg flere", "kloster-touch-multi",
                "Tryk på flere figurer for at tilføje eller fjerne markering uden at flytte dem.", Mode.MULTI, router, group);
        ToggleButton orbit = button("Drej visning", "kloster-touch-orbit",
                "Træk med én finger for at dreje visningen, også oven på figurer.", Mode.ORBIT, router, group);
        ToggleButton pan = button("Flyt visning", "kloster-touch-pan",
                "Træk med én finger for at flytte visningen, også oven på figurer.", Mode.PAN, router, group);
        edit.setSelected(true);
        group.selectedToggleProperty().addListener((obs, oldValue, newValue) -> {
            if (newValue == null) group.selectToggle(oldValue);
        });
        tools.getChildren().setAll(edit, multi, orbit, pan);
        tools.setVisible(true);
    }

    private static ToggleButton button(String label, String id, String help, Mode mode,
            Router router, ToggleGroup group) {
        ToggleButton button = new ToggleButton(label);
        button.setId(id);
        button.getStyleClass().add("kloster-touch-mode");
        button.setMinHeight(44);
        button.setMinWidth(72);
        button.setTooltip(new Tooltip(help));
        button.setAccessibleText(help);
        button.setToggleGroup(group);
        button.setOnAction(event -> router.setMode(mode));
        return button;
    }

    private static boolean insideViewport(Object target, Node viewport) {
        if (!(target instanceof Node)) return false;
        for (Node node = (Node) target; node != null; node = node.getParent()) {
            if (node == viewport) return true;
            // The content root of a SubScene has no JavaFX parent. Match it
            // against the SubScene attached to this viewport instead.
            if (node.getParent() == null && viewport instanceof Pane) {
                for (Node child : ((Pane) viewport).getChildren())
                    if (child instanceof SubScene && ((SubScene) child).getRoot() == node)
                        return true;
            }
        }
        return false;
    }

    @FunctionalInterface public interface CameraSink {
        void move(double dx, double dy, Mode mode);
    }

    /** Event logic is injectable so the routing can be verified without a CAD window. */
    public static final class Router {
        // Keep model taps aligned with Manipulation's native drag threshold.
        // A shorter, deliberate drag must retain its release/save event.
        private static final double MODEL_DRAG_DISTANCE = 3.0;
        private final Function<Node, Object> modelAt;
        private final Predicate<Node> isEmpty;
        private final Predicate<Object> modelSelected;
        private final Consumer<Object> toggleModel;
        private final Consumer<Object> selectOnlyModel;
        private final Runnable clearSelection;
        private final CameraSink camera;
        private final Runnable abort;
        private Mode mode = Mode.EDIT;
        private boolean touchActive, ownsTouch, ownsSyntheticMouse, syntheticModelGesture,
                nativeZoom, canceled, moved, mouseActive, selectedAtPress;
        private boolean consumeMouseTail, tailFromNativeZoom;
        private long mouseTailDeadline;
        private double startX, startY, lastX, lastY;
        private Object pressedModel;

        public Router(Function<Node, Object> modelAt, Predicate<Node> isEmpty,
                Consumer<Object> toggleModel, Runnable clearSelection,
                CameraSink camera, Runnable abort) {
            this(modelAt, isEmpty, model -> false, toggleModel, toggleModel,
                    clearSelection, camera, abort);
        }

        public Router(Function<Node, Object> modelAt, Predicate<Node> isEmpty,
                Predicate<Object> modelSelected, Consumer<Object> toggleModel,
                Consumer<Object> selectOnlyModel, Runnable clearSelection,
                CameraSink camera, Runnable abort) {
            this.modelAt = Objects.requireNonNull(modelAt);
            this.isEmpty = Objects.requireNonNull(isEmpty);
            this.modelSelected = Objects.requireNonNull(modelSelected);
            this.toggleModel = Objects.requireNonNull(toggleModel);
            this.selectOnlyModel = Objects.requireNonNull(selectOnlyModel);
            this.clearSelection = Objects.requireNonNull(clearSelection);
            this.camera = Objects.requireNonNull(camera);
            this.abort = Objects.requireNonNull(abort);
        }

        public Mode getMode() { return mode; }

        public void install(Pane root, Node viewport) {
            root.addEventFilter(TouchEvent.ANY, event -> {
                if (insideViewport(event.getTarget(), viewport)) onTouch(event);
            });
            root.addEventFilter(MouseEvent.ANY, event -> {
                if (insideViewport(event.getTarget(), viewport)) onMouse(event);
            });
            root.addEventFilter(ZoomEvent.ANY, event -> {
                if (insideViewport(event.getTarget(), viewport)) onZoom(event);
            });
        }

        public void setMode(Mode next) {
            Objects.requireNonNull(next);
            if (next == mode) return;
            abort.run();
            boolean interrupted = touchActive || mouseActive || ownsSyntheticMouse || syntheticModelGesture;
            mode = next;
            // A mode switch during a finger gesture owns its remaining events,
            // so the old drag cannot leak into a new mode's model handlers.
            ownsTouch = touchActive;
            mouseActive = ownsSyntheticMouse = syntheticModelGesture = moved = false;
            canceled = true;
            consumeMouseTail = interrupted;
            mouseTailDeadline = System.nanoTime() + 500_000_000L;
            pressedModel = null;
        }

        public void onTouch(TouchEvent event) {
            TouchPoint point = event.getTouchPoint();
            int live = 0;
            for (TouchPoint p : event.getTouchPoints())
                if (p.getState() != TouchPoint.State.RELEASED) live++;
            if (event.getEventType() == TouchEvent.TOUCH_PRESSED && !touchActive) {
                boolean earlyMousePress = syntheticModelGesture;
                Object earlyModel = pressedModel;
                boolean earlySelection = selectedAtPress;
                consumeMouseTail = false;
                tailFromNativeZoom = false;
                ownsSyntheticMouse = false;
                syntheticModelGesture = false;
                nativeZoom = false;
                touchActive = true;
                canceled = moved = false;
                startX = lastX = point.getSceneX();
                startY = lastY = point.getSceneY();
                Node picked = picked(point.getPickResult());
                pressedModel = modelAt.apply(picked);
                selectedAtPress = earlyMousePress && Objects.equals(earlyModel, pressedModel)
                        ? earlySelection : pressedModel != null && modelSelected.test(pressedModel);
                ownsTouch = mode != Mode.EDIT || (pressedModel == null && isEmpty.test(picked));
                if (ownsTouch && live < 2) abort.run();
            }
            if (!touchActive) return;
            if (live >= 2) {
                if (!canceled && ownsTouch) abort.run();
                canceled = true;
                pressedModel = null;
                return;
            }
            if (!canceled && event.getEventType() == TouchEvent.TOUCH_MOVED) {
                double distance = Math.hypot(point.getSceneX() - startX, point.getSceneY() - startY);
                if (pressedModel != null && mode == Mode.EDIT
                        ? distance >= MODEL_DRAG_DISTANCE : distance > 7) moved = true;
            }
            if (ownsTouch && !canceled && event.getEventType() == TouchEvent.TOUCH_MOVED) {
                double x = point.getSceneX(), y = point.getSceneY();
                if (moved && mode != Mode.MULTI) camera.move(x - lastX, y - lastY, mode);
                lastX = x;
                lastY = y;
            }
            if (event.getEventType() == TouchEvent.TOUCH_RELEASED && live == 0) {
                if (ownsTouch && !canceled && !moved
                        && Math.hypot(point.getSceneX() - startX, point.getSceneY() - startY) <= 12) {
                    if (mode == Mode.MULTI && pressedModel != null) toggleModel.accept(pressedModel);
                    else if (mode == Mode.EDIT) clearSelection.run();
                }
                boolean modelTap = !ownsTouch && !canceled && mode == Mode.EDIT
                        && pressedModel != null && !moved
                        && Math.hypot(point.getSceneX() - startX,
                                point.getSceneY() - startY) <= 12;
                if (modelTap) applyDefaultModelTap();
                if (ownsTouch || modelTap) {
                    consumeMouseTail = true;
                    mouseTailDeadline = System.nanoTime() + 500_000_000L;
                }
                touchActive = ownsTouch = false;
                pressedModel = null;
            }
        }

        public void onMouse(MouseEvent event) {
            if (!touchActive && !nativeZoom && event.getEventType() == MouseEvent.MOUSE_PRESSED
                    && !event.isSynthesized()) {
                consumeMouseTail = false;
                tailFromNativeZoom = false;
            }
            // The child TouchGestureGate owns the synthetic click at the end
            // of a native pinch. It must see that one click to release its own
            // synthetic-mouse suppression before the next figure tap.
            if (tailFromNativeZoom && !nativeZoom && event.isSynthesized()
                    && event.getEventType() == MouseEvent.MOUSE_CLICKED) {
                tailFromNativeZoom = false;
                consumeMouseTail = false;
                return;
            }
            if (ownsTouch || nativeZoom
                    || (consumeMouseTail && System.nanoTime() < mouseTailDeadline)) {
                event.consume();
                if (event.getEventType() == MouseEvent.MOUSE_CLICKED) consumeMouseTail = false;
                return;
            }
            consumeMouseTail = false;
            if (touchActive && !ownsTouch && pressedModel != null
                    && event.getEventType() == MouseEvent.MOUSE_DRAGGED
                    && Math.hypot(event.getSceneX() - startX,
                            event.getSceneY() - startY) >= MODEL_DRAG_DISTANCE) moved = true;
            if (mode == Mode.EDIT) {
                if (syntheticModelGesture) {
                    if (event.getEventType() == MouseEvent.MOUSE_DRAGGED
                            && Math.hypot(event.getSceneX() - startX,
                                    event.getSceneY() - startY) >= MODEL_DRAG_DISTANCE) moved = true;
                    if (event.getEventType() == MouseEvent.MOUSE_RELEASED) {
                        boolean modelTap = !moved && pressedModel != null
                                && Math.hypot(event.getSceneX() - startX,
                                        event.getSceneY() - startY) <= 12;
                        if (modelTap) {
                            applyDefaultModelTap();
                            consumeMouseTail = true;
                            mouseTailDeadline = System.nanoTime() + 500_000_000L;
                            event.consume();
                        }
                        syntheticModelGesture = false;
                        pressedModel = null;
                    }
                    return;
                }
                if (ownsSyntheticMouse) {
                    if (event.getEventType() == MouseEvent.MOUSE_DRAGGED) {
                        double x = event.getSceneX(), y = event.getSceneY();
                        if (Math.hypot(x - startX, y - startY) > 7) moved = true;
                        if (moved) camera.move(x - lastX, y - lastY, Mode.ORBIT);
                        lastX = x;
                        lastY = y;
                    } else if (event.getEventType() == MouseEvent.MOUSE_RELEASED) {
                        ownsSyntheticMouse = false;
                        consumeMouseTail = true;
                        mouseTailDeadline = System.nanoTime() + 500_000_000L;
                        if (!moved && Math.hypot(event.getSceneX() - startX,
                                event.getSceneY() - startY) <= 12) clearSelection.run();
                    }
                    event.consume();
                    return;
                }
                if (event.getEventType() == MouseEvent.MOUSE_PRESSED
                        && event.isSynthesized() && event.getButton() == MouseButton.PRIMARY
                        && !touchActive) {
                    Node picked = picked(event.getPickResult());
                    Object model = modelAt.apply(picked);
                    if (model != null) {
                        syntheticModelGesture = true;
                        pressedModel = model;
                        selectedAtPress = modelSelected.test(model);
                        moved = false;
                        startX = event.getSceneX();
                        startY = event.getSceneY();
                    } else if (isEmpty.test(picked)) {
                        ownsSyntheticMouse = true;
                        moved = false;
                        startX = lastX = event.getSceneX();
                        startY = lastY = event.getSceneY();
                        abort.run();
                        event.consume();
                    }
                }
                return;
            }
            if (event.getButton() != MouseButton.PRIMARY && !event.isPrimaryButtonDown()) return;
            if (event.getEventType() == MouseEvent.MOUSE_PRESSED) {
                mouseActive = true;
                canceled = moved = false;
                startX = lastX = event.getSceneX();
                startY = lastY = event.getSceneY();
                pressedModel = modelAt.apply(picked(event.getPickResult()));
                abort.run();
            } else if (event.getEventType() == MouseEvent.MOUSE_DRAGGED && mouseActive) {
                double x = event.getSceneX(), y = event.getSceneY();
                if (Math.hypot(x - startX, y - startY) > 7) moved = true;
                if (moved && mode != Mode.MULTI) camera.move(x - lastX, y - lastY, mode);
                lastX = x;
                lastY = y;
            } else if (event.getEventType() == MouseEvent.MOUSE_RELEASED && mouseActive) {
                if (mode == Mode.MULTI && !moved && pressedModel != null
                        && Math.hypot(event.getSceneX() - startX, event.getSceneY() - startY) <= 12)
                    toggleModel.accept(pressedModel);
                mouseActive = false;
                pressedModel = null;
            }
            event.consume();
        }

        public void onZoom(ZoomEvent event) {
            if (event.getEventType() == ZoomEvent.ZOOM_STARTED
                    || event.getEventType() == ZoomEvent.ZOOM) {
                if (!nativeZoom && ownsSyntheticMouse) abort.run();
                nativeZoom = true;
                ownsSyntheticMouse = false;
                syntheticModelGesture = false;
                pressedModel = null;
                if (touchActive) canceled = true;
            } else if (event.getEventType() == ZoomEvent.ZOOM_FINISHED) {
                boolean hadNativeZoom = nativeZoom;
                nativeZoom = false;
                if (hadNativeZoom) {
                    consumeMouseTail = true;
                    tailFromNativeZoom = true;
                    mouseTailDeadline = System.nanoTime() + 500_000_000L;
                }
            }
        }

        private static Node picked(PickResult result) {
            return result == null ? null : result.getIntersectedNode();
        }

        private void applyDefaultModelTap() {
            if (selectedAtPress) toggleModel.accept(pressedModel);
            else selectOnlyModel.accept(pressedModel);
        }
    }
}
