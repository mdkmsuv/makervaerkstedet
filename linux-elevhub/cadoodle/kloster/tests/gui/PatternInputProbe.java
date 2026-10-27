import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleOperation;
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
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import sun.misc.Unsafe;

/** No-Stage regression probe for the actual pattern handlers and selection handoff. */
public final class PatternInputProbe {
    private static final long LIMIT_MS = 10000;
    private static final List<ExecutorService> EXECUTORS =
            Collections.synchronizedList(new ArrayList<>());

    private static final class Seed extends CaDoodleOperation {
        @Override public String getType() { return "PatternInputSeed"; }
        @Override public List<CSG> process(List<CSG> previous) {
            ArrayList<CSG> next = new ArrayList<>(previous);
            next.add(new Cube(12, 8, 6).toCSG().setName("Source"));
            return next;
        }
        @Override public List<String> getNamesAddedInThisOperation() { return List.of("Source"); }
    }

    private static final class ProjectFixture extends ActiveProject {
        List<CaDoodleOperation> accepted;
        @Override public Thread addOp(CaDoodleOperation operation) {
            accepted.add(operation);
            return get().addOperation(operation);
        }
    }

    private static final class ProbeSession extends SelectionSession {
        ProbeSession() { super(null, null, null); }
        @Override public void updateControlsDisplayOfSelected() { }
    }

    private record Fixture(ProjectFixture project, CaDoodleFile file, ProbeSession session,
                           GateExecutor executor) { }

    private static final class GateExecutor extends java.util.concurrent.AbstractExecutorService {
        private final List<Runnable> pending = new CopyOnWriteArrayList<>();
        private volatile boolean shutdown;
        @Override public void execute(Runnable command) { pending.add(command); }
        void releaseAll() throws Exception {
            for (Runnable task : pending) if (pending.remove(task)) {
                Thread worker=new Thread(task,"pattern-selection"); worker.start();
                worker.join(LIMIT_MS);
                require(!worker.isAlive(),"native selection worker did not complete");
                if(task instanceof java.util.concurrent.Future<?> future) future.get();
            }
        }
        @Override public void shutdown() { shutdown = true; }
        @Override public List<Runnable> shutdownNow() { shutdown = true; List<Runnable> left = new ArrayList<>(pending); pending.clear(); return left; }
        @Override public boolean isShutdown() { return shutdown; }
        @Override public boolean isTerminated() { return shutdown && pending.isEmpty(); }
        @Override public boolean awaitTermination(long timeout, TimeUnit unit) { return isTerminated(); }
    }

    public static void main(String[] args) throws Exception {
        Path root = args.length == 1 ? Path.of(args[0]).toAbsolutePath()
                : Files.createTempDirectory("cadoodle-pattern-input-");
        Files.createDirectories(root);
        System.setProperty("user.home", Files.createDirectories(root.resolve("home")).toString());
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        Platform.setImplicitExit(false);
        Platform.startup(() -> { });
        List<String> failures = new CopyOnWriteArrayList<>();
        try {
            runCase("empty-selection", failures, () -> checkEmptySelectionDoesNotCommit(fixture(root, "empty")));
            runCase("linear-copy-list-order", failures, () -> checkCopyListFollowsNativeSelection(fixture(root, "linear-copy"),false));
            runCase("radial-copy-list-order", failures, () -> checkCopyListFollowsNativeSelection(fixture(root, "radial-copy"),true));
            System.out.println("SUMMARY: " + (3 - failures.size()) + "/3 pattern cases passed; " + failures.size() + " failed");
            if (!failures.isEmpty()) throw new AssertionError(String.join("; ", failures));
        } finally {
            for (ExecutorService executor : EXECUTORS) executor.shutdownNow();
            Platform.exit();
        }
    }

    private static Fixture fixture(Path root, String name) throws Exception {
        CaDoodleFile file = new CaDoodleFile().setSelf(root.resolve(name + ".doodle").toFile())
                .setProjectName(name);
        file.initialize();
        join(file.addOperation(new Seed()));
        ProjectFixture project = (ProjectFixture) unsafe().allocateInstance(ProjectFixture.class);
        project.accepted = new CopyOnWriteArrayList<>();
        setField(ActiveProject.class, project, "fromFile", file);
        ProbeSession session = (ProbeSession) unsafe().allocateInstance(ProbeSession.class);
        setField(SelectionSession.class, session, "ap", project);
        setField(SelectionSession.class, session, "meshes", new HashMap<>());
        setField(SelectionSession.class, session, "selectionListeners", new CopyOnWriteArrayList<>());
        GateExecutor executor = new GateExecutor();
        EXECUTORS.add(executor);
        setField(SelectionSession.class, session, "executor", executor);
        session.setUpdateRobotLab(() -> { });
        return new Fixture(project, file, session, executor);
    }

    private static void checkEmptySelectionDoesNotCommit(Fixture fixture) throws Exception {
        int before = fixture.project.accepted.size();
        fx(() -> {
            fixture.session.setSelected(new LinkedHashSet<>());
            fixture.session.runHexDistribute();
            fixture.session.runBoltHole();
        });
        idle(fixture.project);
        require(fixture.project.accepted.size() == before,
                "empty selection still committed pattern operation(s): "
                        + (fixture.project.accepted.size() - before));
    }

    private static void checkCopyListFollowsNativeSelection(Fixture fixture,boolean radial) throws Exception {
        CSG source = fixture.file.getCurrentState().stream()
                .filter(shape -> shape.getName().equals("Source")).findFirst().orElseThrow();
        fx(() -> {
            fixture.session.setSelected(new LinkedHashSet<>(List.of(source)));
            if(radial) fixture.session.runBoltHole(); else fixture.session.runHexDistribute();
        });
        idle(fixture.project);
        fixture.executor.releaseAll();
        settleFx();
        List<String> selected = fixture.session.selectedSnapshot();
        @SuppressWarnings("unchecked")
        List<String> copied = (List<String>) getField(SelectionSession.class, fixture.session, "copySetinternal");
        require(fixture.project.accepted.size()==1,"pattern did not commit once");
        require(selected.size()>1 && !selected.contains("Source"), "pattern operation produced no selectable copies");
        require(selected.equals(copied), "copy list raced native selectAll: selected=" + selected
                + " copied=" + copied);
    }

    private static void idle(ActiveProject project) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(LIMIT_MS);
        while (ModelEditQueue.isBusy(project)) {
            require(System.nanoTime() < deadline, "model queue did not drain");
            Thread.sleep(10);
        }
    }

    private static void settleFx() throws Exception { fx(() -> { }); fx(() -> { }); }
    private static void runCase(String name, List<String> failures, ThrowingAction action) {
        try { action.run(); System.out.println("PASS: " + name); }
        catch (Throwable error) { failures.add(name + ": " + error); System.out.println("FAIL: " + name + ": " + error); error.printStackTrace(); }
    }
    @FunctionalInterface private interface ThrowingAction { void run() throws Exception; }
    private static void join(Thread task) throws Exception {
        require(task != null, "seed operation was rejected");
        task.join(LIMIT_MS);
        require(!task.isAlive(), "seed operation did not finish");
    }
    private static void fx(Runnable task) throws Exception {
        CountDownLatchBox done = new CountDownLatchBox();
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.runLater(() -> { try { task.run(); } catch (Throwable error) { failure.set(error); } finally { done.countDown(); } });
        require(done.await(LIMIT_MS), "FX callback timed out");
        if (failure.get() != null) throw new AssertionError("FX callback failed", failure.get());
    }
    private static final class CountDownLatchBox {
        private final java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(1);
        void countDown() { latch.countDown(); }
        boolean await(long ms) throws InterruptedException { return latch.await(ms, TimeUnit.MILLISECONDS); }
    }
    private static Object getField(Class<?> owner, Object target, String name) throws Exception {
        Field field = owner.getDeclaredField(name); field.setAccessible(true); return field.get(target);
    }
    private static void setField(Class<?> owner, Object target, String name, Object value) throws Exception {
        Field field = owner.getDeclaredField(name); field.setAccessible(true); field.set(target, value);
    }
    private static Unsafe unsafe() throws Exception {
        Field field = Unsafe.class.getDeclaredField("theUnsafe"); field.setAccessible(true); return (Unsafe) field.get(null);
    }
    private static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
}
