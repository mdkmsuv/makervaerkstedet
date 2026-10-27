import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.kloster.DimensionResize;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.commonwealthrobotics.controls.SelectionSession;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleOperation;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Resize;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import javafx.application.Platform;
import java.lang.reflect.Field;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import sun.misc.Unsafe;

/** Bounded, no-Stage native geometry check for toolbar order and captured selection. */
public final class MutationOrderProbe {
    private static final long LIMIT_MS = 10000;
    private static final List<ExecutorService> EXECUTORS =
            Collections.synchronizedList(new ArrayList<>());
    private static final class ProbeProject extends ActiveProject {
        @Override public Thread addOp(CaDoodleOperation operation) {
            return get().addOperation(operation);
        }
    }
    private static final class ProbeSession extends SelectionSession {
        ProbeSession() { super(null, null, null); }
        @Override public void updateControlsDisplayOfSelected() { }
    }
    private static final class Shapes extends CaDoodleOperation {
        @Override public String getType() { return "MutationOrderShapes"; }
        @Override public List<CSG> process(List<CSG> previous) {
            ArrayList<CSG> state = new ArrayList<>(previous);
            state.add(new Cube(20, 16, 10).toCSG().setName("First"));
            state.add(new Cube(8, 8, 8).toCSG().movex(50).setName("Second"));
            return state;
        }
        @Override public List<String> getNamesAddedInThisOperation() {
            return List.of("First", "Second");
        }
    }
    private record Fixture(ProbeProject active, CaDoodleFile file, ProbeSession session) { }

    public static void main(String[] args) throws Exception {
        Path root = args.length == 1 ? Path.of(args[0]).toAbsolutePath()
                : Files.createTempDirectory("cadoodle-mutation-order-");
        Files.createDirectories(root);
        System.setProperty("user.home", Files.createDirectories(root.resolve("home")).toString());
        CaDoodleFile.setImageEngine((db, shapes, file) ->
                new javafx.scene.image.WritableImage(1, 1));
        Platform.setImplicitExit(false);
        Platform.startup(() -> { });
        try {
            checkDeleteAfterResize(fixture(root, "delete"));
            Fixture grouped = fixture(root, "group");
            checkGroupAfterResize(grouped);
            checkUngroupAfterPendingWork(grouped);
            checkDoubleLockToggle(fixture(root, "toggle"));
            System.out.println("PASS: captured Delete/Group/Ungroup and queued lock toggles");
        } finally {
            for (ExecutorService executor : EXECUTORS) executor.shutdownNow();
            Platform.exit();
        }
    }

    private static Fixture fixture(Path root, String name) throws Exception {
        CaDoodleFile file = new CaDoodleFile()
                .setSelf(root.resolve(name + ".doodle").toFile()).setProjectName(name);
        file.initialize();
        join(file.addOperation(new Shapes()));
        ProbeProject active = (ProbeProject) unsafe().allocateInstance(ProbeProject.class);
        Field field = ActiveProject.class.getDeclaredField("fromFile");
        field.setAccessible(true);
        field.set(active, file);
        ProbeSession session = (ProbeSession) unsafe().allocateInstance(ProbeSession.class);
        setField(SelectionSession.class, session, "ap", active);
        setField(SelectionSession.class, session, "meshes", new HashMap<>());
        setField(SelectionSession.class, session, "selectionListeners", new CopyOnWriteArrayList<>());
        ExecutorService executor = Executors.newSingleThreadExecutor();
        EXECUTORS.add(executor);
        setField(SelectionSession.class, session, "executor", executor);
        session.setUpdateRobotLab(() -> { });
        session.setSelected(new LinkedHashSet<>(file.getCurrentState()));
        return new Fixture(active, file, session);
    }

    private static void checkDeleteAfterResize(Fixture fixture) throws Exception {
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicReference<Resize> resized = new AtomicReference<>();
        fx(() -> {
            ModelEditQueue.submit(fixture.active, () -> { entered.countDown(); await(release); });
            ModelEditQueue.submit(fixture.active, () -> {
                Resize operation = resizeFirst(fixture.file);
                resized.set(operation);
                join(fixture.active.addOp(operation));
            });
            fixture.session.setSelected(new LinkedHashSet<>(List.of(get(fixture.file, "First"))));
            fixture.session.onDelete();
            fixture.session.setSelected(new LinkedHashSet<>(List.of(get(fixture.file, "Second"))));
        });
        require(entered.await(LIMIT_MS, TimeUnit.MILLISECONDS), "queue blocker did not start");
        require(fixture.file.getCurrentState().size() == 2, "native mutation bypassed pending queue");
        release.countDown();
        idle(fixture.active);
        require(fixture.file.getCurrentState().stream().noneMatch(c -> c.getName().equals("First")),
                "Delete missed the FX-captured First shape");
        require(fixture.file.getCurrentState().stream().anyMatch(c -> c.getName().equals("Second")),
                "Delete followed mutable selection and removed Second");
        CSG beforeDelete = CaDoodleFile.getByName(
                fixture.file.getStateAtOperation(resized.get()), "First");
        require(close(beforeDelete.getTotalX(), 24), "resize was not committed before Delete");
    }

    private static void checkGroupAfterResize(Fixture fixture) throws Exception {
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        fx(() -> {
            ModelEditQueue.submit(fixture.active, () -> { entered.countDown(); await(release); });
            ModelEditQueue.submit(fixture.active, () ->
                    join(fixture.active.addOp(resizeFirst(fixture.file))));
            fixture.session.onGroup(false, false);
        });
        require(entered.await(LIMIT_MS, TimeUnit.MILLISECONDS), "group blocker did not start");
        require(fixture.file.getCurrentState().stream().noneMatch(CSG::isGroupResult),
                "Group bypassed pending resize");
        release.countDown();
        idle(fixture.active);
        require(fixture.file.getCurrentState().stream().anyMatch(CSG::isGroupResult),
                "Group produced no result");
        CSG member = CaDoodleFile.getByName(fixture.file.getCurrentState(), "First");
        require(member.isInGroup() && close(member.getTotalX(), 24),
                "Group did not include the resized native geometry");
        CSG result = fixture.file.getCurrentState().stream().filter(CSG::isGroupResult)
                .findFirst().orElseThrow();
        require(result.getTotalX() > 50, "Group result did not include both resized members");
        require(fixture.session.selectedSnapshot().equals(List.of(result.getName())),
                "Group completion did not select its result");
    }

    private static void checkUngroupAfterPendingWork(Fixture fixture) throws Exception {
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        fx(() -> {
            CSG groupResult = fixture.file.getCurrentState().stream()
                    .filter(CSG::isGroupResult).findFirst().orElseThrow();
            fixture.session.setSelected(new LinkedHashSet<>(List.of(groupResult)));
            ModelEditQueue.submit(fixture.active, () -> { entered.countDown(); await(release); });
            fixture.session.onUngroup();
            fixture.session.setSelected(new LinkedHashSet<>(List.of(get(fixture.file, "Second"))));
        });
        require(entered.await(LIMIT_MS, TimeUnit.MILLISECONDS), "ungroup blocker did not start");
        require(fixture.file.getCurrentState().stream().anyMatch(CSG::isGroupResult),
                "Ungroup bypassed pending model work");
        release.countDown();
        idle(fixture.active);
        require(fixture.file.getCurrentState().stream().noneMatch(CSG::isGroupResult),
                "Ungroup did not use the FX-captured group selection");
        require(!get(fixture.file, "First").isInGroup()
                && !get(fixture.file, "Second").isInGroup(),
                "Ungroup did not release native group members");
        require(fixture.session.selectedSnapshot().containsAll(List.of("First", "Second")),
                "Ungroup completion did not select its released members");
    }

    private static void checkDoubleLockToggle(Fixture fixture) throws Exception {
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        int before = fixture.file.getOperations().size();
        fx(() -> {
            fixture.session.setSelected(new LinkedHashSet<>(List.of(get(fixture.file, "First"))));
            ModelEditQueue.submit(fixture.active, () -> { entered.countDown(); await(release); });
            fixture.session.lockToggle();
            fixture.session.lockToggle();
        });
        require(entered.await(LIMIT_MS, TimeUnit.MILLISECONDS), "toggle blocker did not start");
        require(!get(fixture.file, "First").isLock(), "lock toggle bypassed pending work");
        release.countDown();
        idle(fixture.active);
        require(!get(fixture.file, "First").isLock(), "two lock toggles did not restore unlocked state");
        require(fixture.file.getOperations().size() == before + 2,
                "both queued lock toggles did not create native history entries");
    }

    private static Resize resizeFirst(CaDoodleFile file) {
        try {
            CSG shape = CaDoodleFile.getByName(file.getCurrentState(), "First");
            return DimensionResize.create(List.of(shape), new TransformNR(), 0, 24,
                    .5, .5, false);
        } catch (Exception error) {
            throw new RuntimeException(error);
        }
    }
    private static CSG get(CaDoodleFile file, String name) {
        try { return CaDoodleFile.getByName(file.getCurrentState(), name); }
        catch (Exception error) { throw new RuntimeException(error); }
    }
    private static void setField(Class<?> owner, Object target, String name, Object value)
            throws Exception {
        Field field = owner.getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }

    private static Unsafe unsafe() throws Exception {
        Field field = Unsafe.class.getDeclaredField("theUnsafe");
        field.setAccessible(true);
        return (Unsafe) field.get(null);
    }
    private static void fx(Runnable task) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.runLater(() -> {
            try { task.run(); } catch (Throwable error) { failure.set(error); }
            finally { done.countDown(); }
        });
        require(done.await(LIMIT_MS, TimeUnit.MILLISECONDS), "FX callback timed out");
        if (failure.get() != null) throw new AssertionError("FX callback failed", failure.get());
    }
    private static void idle(ActiveProject project) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(LIMIT_MS);
        while (ModelEditQueue.isBusy(project)) {
            require(System.nanoTime() < deadline, "model queue did not drain");
            Thread.sleep(10);
        }
    }
    private static void join(Thread task) {
        if (task == null) throw new AssertionError("native operation was rejected");
        try {
            task.join(LIMIT_MS);
            require(!task.isAlive(), "native operation timed out");
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new AssertionError(error);
        }
    }
    private static void await(CountDownLatch latch) {
        try { require(latch.await(LIMIT_MS, TimeUnit.MILLISECONDS), "blocker timed out"); }
        catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new AssertionError(error);
        }
    }
    private static boolean close(double actual, double expected) {
        return Math.abs(actual - expected) < 1e-4;
    }
    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
