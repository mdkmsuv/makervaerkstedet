import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.RulerManager;
import com.commonwealthrobotics.controls.*;
import com.commonwealthrobotics.numbers.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.*;
import javafx.application.Platform;
import javafx.scene.control.TextField;
import javafx.scene.input.*;
import javafx.scene.transform.Affine;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

/** Actual text input -> ResizeSessionManager -> queue -> native Resize, no Stage. */
public final class DimensionCommitProbe {
    static class FileStub extends CaDoodleFile {
        volatile List<CSG> state;
        @Override public List<CSG> getCurrentState() { return state; }
        @Override public TransformNR getWorkplane() { return new TransformNR(); }
        @Override public boolean isOperationRunning() { return false; }
    }
    static class ProjectStub extends ActiveProject {
        FileStub file;
        CountDownLatch firstEntered, releaseFirst, secondDone;
        AtomicReference<Throwable> failure;
        int count;
        @Override public CaDoodleFile get() { return file; }
        @Override public Thread addOp(CaDoodleOperation operation) {
            int order = ++count;
            Thread task = new Thread(() -> {
                try {
                    if (order == 1) { firstEntered.countDown(); require(releaseFirst.await(5, TimeUnit.SECONDS), "first timeout"); }
                    operation.setCaDoodleFile(file);
                    file.state = operation.process(file.state);
                } catch (Throwable error) { failure.set(error); }
                finally { if (order == 2) secondDone.countDown(); }
            });
            task.setDaemon(true); task.start(); return task;
        }
    }
    static class SelectionStub extends SelectionSession {
        List<String> names;
        SelectionStub() { super(null, null, null); }
        @Override public boolean isLocked() { return false; }
        @Override public List<String> selectedSnapshot() { return names; }
    }
    static class ControlsStub extends ControlSprites {
        ControlsStub() { super(null, null, null, null, null, null, null); }
        @Override public void setMode(SpriteDisplayMode mode) { }
    }
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> {});
        try {
            var field = sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); field.setAccessible(true);
            var unsafe = (sun.misc.Unsafe) field.get(null);
            ProjectStub project = (ProjectStub) unsafe.allocateInstance(ProjectStub.class);
            project.file = new FileStub();
            project.file.state = List.of(new Cube(31, 20, 20).toCSG().setName("selected"),
                new Cube(8).toCSG().movex(100).setName("other"));
            project.firstEntered = new CountDownLatch(1); project.releaseFirst = new CountDownLatch(1);
            project.secondDone = new CountDownLatch(1); project.failure = new AtomicReference<>();
            SelectionStub selection = (SelectionStub) unsafe.allocateInstance(SelectionStub.class);
            selection.names = List.of("selected");
            ResizeSessionManager manager = (ResizeSessionManager) unsafe.allocateInstance(ResizeSessionManager.class);
            set(manager, "activeProject", project); set(manager, "session", selection);
            set(manager, "resizeAllowed", true); set(manager, "controls", List.of());
            set(manager, "controlSprites", unsafe.allocateInstance(ControlsStub.class));
            var handle = (ResizingHandle) unsafe.allocateInstance(ResizingHandle.class);
            for (String name : List.of("leftFront", "leftRear", "rightFront", "rightRear", "frontMid", "rearMid", "leftMid", "rightMid")) set(manager, name, handle);
            runFx(() -> {
                ThreedNumber x = number(manager, 0), z = number(manager, 2);
                TextField xf = (TextField)x.getTextField().getFirst(), zf = (TextField)z.getTextField().getFirst();
                x.setValue(31); z.setValue(20); x.show(); z.show();
                xf.setText("50.0"); zf.setText("35,0");
                xf.fireEvent(new KeyEvent(KeyEvent.KEY_PRESSED, "", "", KeyCode.ENTER, false, false, false, false));
                z.hide(); // A model refresh hides another edited field before the first operation returns.
                selection.names = List.of("other"); // queued edits retain their captured selection
            });
            require(project.firstEntered.await(5, TimeUnit.SECONDS), "X did not start");
            require(!project.secondDone.await(150, TimeUnit.MILLISECONDS), "Z bypassed X");
            project.releaseFirst.countDown();
            require(project.secondDone.await(5, TimeUnit.SECONDS), "Z did not complete");
            if (project.failure.get() != null) throw new AssertionError(project.failure.get());
            require(project.count == 2, "expected exactly two native commits");
            Bounds bounds = project.file.state.get(0).getBounds();
            near(bounds.getTotalX(), 50); near(bounds.getTotalY(), 20); near(bounds.getTotalZ(), 35);
            near(project.file.state.get(1).getBounds().getTotalX(), 8);
            System.out.println("PASS: real numeric input/resize queue preserves rapid X and Z, immutable selection and untouched objects");
        } finally { Platform.exit(); }
    }
    private static ThreedNumber number(ResizeSessionManager manager, int axis) {
        ThreedNumber[] result = new ThreedNumber[1];
        result[0] = new ThreedNumber(new Affine(), new Affine(), () -> {
            if (!result[0].canceled) try { manager.setDimension(axis, result[0].getMostRecentValue()); }
            catch (Exception error) { throw new AssertionError(error); }
        }, TextFieldDimension.None, new RulerManager(null), 4, Vector3d.xyz(1,0,0));
        return result[0];
    }
    private static void set(Object target, String name, Object value) throws Exception {
        var field = ResizeSessionManager.class.getDeclaredField(name); field.setAccessible(true); field.set(target, value);
    }
    private static void runFx(Runnable action) throws Exception {
        var task = new FutureTask<Void>(action, null); Platform.runLater(task); task.get(10, TimeUnit.SECONDS);
    }
    private static void near(double actual, double expected) { require(Math.abs(actual-expected)<1e-5, actual + " != " + expected); }
    private static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
}
