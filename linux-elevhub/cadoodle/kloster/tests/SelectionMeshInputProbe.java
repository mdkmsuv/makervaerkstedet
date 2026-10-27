import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.WorkplaneManager;
import com.commonwealthrobotics.controls.ControlSprites;
import com.commonwealthrobotics.controls.SelectionSession;
import com.commonwealthrobotics.controls.SpriteDisplayMode;
import com.commonwealthrobotics.kloster.TouchInteractionModes;
import com.neuronrobotics.bowlerkernel.Bezier3d.Manipulation;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.geometry.Point3D;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.PickResult;
import javafx.scene.input.TouchEvent;
import javafx.scene.input.TouchPoint;
import javafx.scene.Group;
import javafx.scene.Node;
import javafx.scene.SubScene;
import javafx.scene.layout.Pane;
import javafx.scene.shape.MeshView;
import javafx.scene.transform.Affine;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.concurrent.FutureTask;

/**
 * No-Stage audit of SelectionSession's actual MeshView press handler.
 *
 * This deliberately bypasses application construction only: the production
 * setUpControls method and its event-filter lambda receive real MeshViews and
 * real PickResults. It does not prove JavaFX's desktop/subscene 3D hit-test,
 * physical touch driver delivery, or the full controls layout.
 */
public final class SelectionMeshInputProbe {
    private SelectionMeshInputProbe() { }

    private static final class WorkplaneStub extends WorkplaneManager {
        boolean active;
        WorkplaneStub() { super(null, null, null); }
        @Override public boolean isActive() { return active; }
    }

    private static final class ControlsStub extends ControlSprites {
        int modes;
        double height;
        ControlsStub() { super(null, null, null, null, null, null, null); }
        @Override public void setMode(SpriteDisplayMode mode) { modes++; }
        @Override public void setObjectHeight(double value) { height = value; }
        @Override public void abortInteraction() { }
        @Override public void cancelOperationMode() { }
    }

    private static final class FileStub extends com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile {
        @Override public TransformNR getWorkplane() { return new TransformNR(); }
        @Override public boolean isOperationRunning() { return false; }
    }

    private static final class ProjectStub extends ActiveProject {
        private FileStub file;
        @Override public com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile get() { return file; }
    }

    private static final class SessionStub extends SelectionSession {
        int updates;
        int touchToggles;
        int touchSelectOnly;
        SessionStub() { super(null, null, null); }
        @Override public void updateControlsDisplayOfSelected() { updates++; }
        @Override public void setMode(SpriteDisplayMode mode) { }
        @Override public boolean moveLock() { return false; }
        @Override public void toggleTouchSelection(CSG model) {
            touchToggles++;
            super.toggleTouchSelection(model);
        }
        @Override public void selectOnlyTouchModel(CSG model) {
            touchSelectOnly++;
            super.selectOnlyTouchModel(model);
        }
        @Override public void updateControls() { }
        @Override public void cancelOperationModes() { }
        @Override public eu.mihosoft.vrl.v3d.Bounds getSellectedBounds() {
            return getSelected().isEmpty() ? new Cube(1).toCSG().getBounds()
                    : getSelected().iterator().next().getBounds();
        }
    }

    public static void main(String[] args) throws Exception {
        Platform.startup(() -> { });
        try {
            FutureTask<Void> task = new FutureTask<>(() -> { run(); return null; });
            Platform.runLater(task);
            task.get();
        } finally {
            Platform.exit();
        }
        System.out.println("PASS: actual MeshView selection, origin, Shift/workplane/group gates; real touch tap toggles and 3/4 px drags retain native save");
    }

    private static void run() throws Exception {
        var unsafeField = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
        unsafeField.setAccessible(true);
        var unsafe = (sun.misc.Unsafe) unsafeField.get(null);

        SessionStub session = (SessionStub) unsafe.allocateInstance(SessionStub.class);
        WorkplaneStub workplane = (WorkplaneStub) unsafe.allocateInstance(WorkplaneStub.class);
        ControlsStub controls = (ControlsStub) unsafe.allocateInstance(ControlsStub.class);
        ProjectStub project = (ProjectStub) unsafe.allocateInstance(ProjectStub.class);
        project.file = (FileStub) unsafe.allocateInstance(FileStub.class);
        set(session, "workplane", workplane);
        set(session, "controls", controls);
        set(session, "ap", project);
        set(session, "updateRobotLab", (Runnable) () -> { });
        set(session, "selectionListeners", new java.util.concurrent.CopyOnWriteArrayList<Runnable>());
        set(session, "manipulation", new Manipulation(new Affine(), new eu.mihosoft.vrl.v3d.Vector3d(1, 1, 0),
                new TransformNR(), (x, y, grid) -> new Point3D(x, y, 0), false, false));
        set(session, "selected", new LinkedHashSet<CSG>());

        CSG first = new Cube(10, 12, 14).toCSG().setName("first");
        CSG grouped = new Cube(8, 8, 8).toCSG().setName("grouped").addGroupMembership("g");
        MeshView firstView = first.newMesh();
        MeshView firstHalo = first.newMesh(true);
        MeshView groupedView = grouped.newMesh();
        MeshView groupedHalo = grouped.newMesh(true);
        HashMap<CSG, SelectionSession.MeshHolder> meshes = new HashMap<>();
        meshes.put(first, session.new MeshHolder(firstView, firstHalo, first.getBounds()));
        meshes.put(grouped, session.new MeshHolder(groupedView, groupedHalo, grouped.getBounds()));
        set(session, "meshes", meshes);

        Method setup = SelectionSession.class.getDeclaredMethod("setUpControls", MeshView.class, CSG.class);
        setup.setAccessible(true);
        Manipulation move = (Manipulation) get(session, "manipulation");
        firstView.addEventFilter(MouseEvent.ANY, move.getMouseEvents());
        setup.invoke(session, firstView, first);
        setup.invoke(session, groupedView, grouped);

        fire(firstView, MouseEvent.MOUSE_PRESSED, 2, 3, false);
        require(session.getSelected().size() == 1 && session.getSelected().contains(first),
                "plain MeshView press did not select the picked CSG");
        require(controls.height > 0, "selection handler did not update object height");
        require(session.updates == 1, "selection handler did not request UI update");
        Point3D origin = (Point3D) get(session, "startingPosition3D");
        near(origin.getX(), 2, "picked surface origin X");
        near(origin.getY(), 3, "picked surface origin Y");

        fire(firstView, MouseEvent.MOUSE_PRESSED, 4, 5, true);
        require(session.getSelected().isEmpty(), "Shift press did not toggle selected MeshView off");
        fire(firstView, MouseEvent.MOUSE_PRESSED, 4, 5, false);
        require(session.getSelected().size() == 1, "second plain press did not reselect MeshView");
        origin = (Point3D) get(session, "startingPosition3D");

        // A PickResult for another node must not initialize the move origin.
        MeshView other = new Cube(3).toCSG().newMesh();
        fireWithPick(firstView, other, MouseEvent.MOUSE_PRESSED, 7, 8, false);
        require(session.getSelected().size() == 1, "foreign PickResult unexpectedly changed selection");
        Point3D retained = (Point3D) get(session, "startingPosition3D");
        near(retained.getX(), origin.getX(), "foreign PickResult changed move origin X");
        near(retained.getY(), origin.getY(), "foreign PickResult changed move origin Y");

        fire(groupedView, MouseEvent.MOUSE_PRESSED, 1, 1, false);
        require(session.getSelected().isEmpty(), "group member was selectable through native handler");

        workplane.active = true;
        fire(firstView, MouseEvent.MOUSE_PRESSED, 1, 1, false);
        require(session.getSelected().isEmpty(), "active workplane did not gate MeshView press");
        workplane.active = false;

        // A real Pane -> SubScene -> MeshView chain exercises TouchInteractionModes.Router
        // alongside the production SelectionSession press filter. Synthetic model taps
        // must alternate select, deselect, select; the native handler still receives the
        // press, while Router owns the release tail.
        Pane root = new Pane();
        Pane viewport = new Pane();
        Group sceneRoot = new Group(firstView);
        SubScene subScene = new SubScene(sceneRoot, 240, 180);
        viewport.getChildren().add(subScene);
        root.getChildren().add(viewport);
        TouchInteractionModes.Router router = new TouchInteractionModes.Router(
                node -> node == firstView ? first : null,
                node -> node == null || node == subScene || node == sceneRoot,
                model -> session.getSelected().contains(model),
                model -> session.toggleTouchSelection((CSG) model),
                model -> session.selectOnlyTouchModel((CSG) model),
                () -> session.getSelected().clear(),
                (dx, dy, mode) -> { }, session::abortInteraction);
        router.install(root, viewport);
        session.getSelected().clear();
        syntheticTap(firstView, 20, 20);
        require(session.getSelected().contains(first), "synthetic touch tap did not select");
        Thread.sleep(550);
        syntheticTap(firstView, 20, 20);
        require(session.getSelected().isEmpty(), "second synthetic touch tap did not deselect (toggle="
                + session.touchToggles + ",selectOnly=" + session.touchSelectOnly + ")");
        Thread.sleep(550);
        syntheticTap(firstView, 20, 20);
        require(session.getSelected().contains(first), "third synthetic touch tap did not reselect");
        require(session.touchSelectOnly >= 2 && session.touchToggles >= 1,
                "router did not route native model tap callbacks");

        session.getSelected().clear();
        rawTap(firstView, 20, 20);
        require(session.getSelected().contains(first), "raw touch first tap did not select");
        rawTap(firstView, 20, 20);
        require(session.getSelected().isEmpty(), "raw touch second tap did not deselect");
        rawTap(firstView, 20, 20);
        require(session.getSelected().contains(first), "raw touch third tap did not reselect");

        // Synthetic touch streams must preserve the primary-button-down bit on drag;
        // the real Manipulation filter must commit even at the small 3-4 px child
        // touch movement threshold instead of being treated as a tap. Update 11
        // deliberately fails here because Router used a >7 px model threshold.
        final int[] saves = {0};
        move.addSaveSnapshotListener(() -> saves[0]++);
        syntheticDrag(firstView, 4, 0);
        require(saves[0] == 1 && session.getSelected().contains(first),
                "4 px synthetic model drag did not commit exactly once (saves=" + saves[0] + ")");
        syntheticDrag(firstView, 3, 0);
        require(saves[0] == 2 && session.getSelected().contains(first),
                "3 px synthetic model drag did not commit exactly once (saves=" + saves[0] + ")");
        rawDrag(firstView, 3, true);
        require(saves[0] == 3 && session.getSelected().contains(first),
                "3 px raw model drag lost selection or native save");
        rawDrag(firstView, 4, false);
        require(saves[0] == 4 && session.getSelected().contains(first),
                "missing raw move event lost selection or native save");
    }

    private static void fire(MeshView target, javafx.event.EventType<MouseEvent> type,
                             double x, double y, boolean shift) {
        fireWithPick(target, target, type, x, y, shift);
    }

    private static void syntheticTap(MeshView target, double x, double y) {
        fireWithPick(target, target, MouseEvent.MOUSE_PRESSED, x, y, false, true, true);
        fireWithPick(target, target, MouseEvent.MOUSE_RELEASED, x, y, false, false, true);
        fireWithPick(target, target, MouseEvent.MOUSE_CLICKED, x, y, false, false, true);
    }

    private static void syntheticDrag(MeshView target, double dx, double dy) {
        fireWithPick(target, target, MouseEvent.MOUSE_PRESSED, 40, 40, false, true, true);
        fireWithPick(target, target, MouseEvent.MOUSE_DRAGGED, 40 + dx, 40 + dy, false, true, true);
        fireWithPick(target, target, MouseEvent.MOUSE_RELEASED, 40 + dx, 40 + dy, false, false, true);
        fireWithPick(target, target, MouseEvent.MOUSE_CLICKED, 40 + dx, 40 + dy, false, false, true);
    }

    private static void rawTap(MeshView target, double x, double y) {
        touch(target,TouchEvent.TOUCH_PRESSED,TouchPoint.State.PRESSED,x,y);
        fireWithPick(target,target,MouseEvent.MOUSE_PRESSED,x,y,false,true,true);
        touch(target,TouchEvent.TOUCH_RELEASED,TouchPoint.State.RELEASED,x,y);
        fireWithPick(target,target,MouseEvent.MOUSE_RELEASED,x,y,false,false,true);
        fireWithPick(target,target,MouseEvent.MOUSE_CLICKED,x,y,false,false,true);
    }

    private static void rawDrag(MeshView target, double dx, boolean rawMove) {
        touch(target,TouchEvent.TOUCH_PRESSED,TouchPoint.State.PRESSED,40,40);
        fireWithPick(target,target,MouseEvent.MOUSE_PRESSED,40,40,false,true,true);
        if (rawMove) touch(target,TouchEvent.TOUCH_MOVED,TouchPoint.State.MOVED,40+dx,40);
        fireWithPick(target,target,MouseEvent.MOUSE_DRAGGED,40+dx,40,false,true,true);
        touch(target,TouchEvent.TOUCH_RELEASED,TouchPoint.State.RELEASED,40+dx,40);
        fireWithPick(target,target,MouseEvent.MOUSE_RELEASED,40+dx,40,false,false,true);
        fireWithPick(target,target,MouseEvent.MOUSE_CLICKED,40+dx,40,false,false,true);
    }

    private static int touchSet;
    private static void touch(MeshView target, javafx.event.EventType<TouchEvent> type,
                              TouchPoint.State state, double x, double y) {
        var point=new TouchPoint(1,state,x,y,x,y,target,new PickResult(target,new Point3D(x,y,0),1));
        Event.fireEvent(target,new TouchEvent(type,point,java.util.List.of(point),++touchSet,
                false,false,false,false));
    }

    private static void fireWithPick(MeshView target, MeshView picked,
                                     javafx.event.EventType<MouseEvent> type,
                                     double x, double y, boolean shift) {
        fireWithPick(target, picked, type, x, y, shift, type != MouseEvent.MOUSE_RELEASED, false);
    }

    private static void fireWithPick(MeshView target, MeshView picked,
                                     javafx.event.EventType<MouseEvent> type,
                                     double x, double y, boolean shift,
                                     boolean primaryDown, boolean synthesized) {
        Event.fireEvent(target, new MouseEvent(type, x, y, x, y, MouseButton.PRIMARY, 1,
                shift, false, false, false, primaryDown, false, false, synthesized, false, true,
                new PickResult(picked, new Point3D(x, y, 0), 1)));
    }

    private static Object get(Object target, String name) throws Exception {
        Field field = SelectionSession.class.getDeclaredField(name);
        field.setAccessible(true);
        return field.get(target);
    }

    private static void near(double actual, double expected, String message) {
        require(Math.abs(actual - expected) < 1e-6,
                message + ": " + actual + " != " + expected);
    }

    private static void set(Object target, String name, Object value) throws Exception {
        Field field = SelectionSession.class.getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }
}
