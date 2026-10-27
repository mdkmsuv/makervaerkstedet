import com.commonwealthrobotics.kloster.ModelEditQueue;
import javafx.application.Platform;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.lang.reflect.Field;
import sun.misc.Unsafe;

/** Bounded no-Stage concurrency regression for ModelEditQueue. */
public final class ModelEditQueueProbe {
    private static final long LIMIT_MS = 4000;
    private static final class StubFile extends com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile {
        volatile boolean running;
        StubFile(boolean running) { super(); this.running = running; }
        @Override public boolean isOperationRunning() { return running; }
    }
    private static final class ProjectFixture {
        final com.commonwealthrobotics.ActiveProject project;
        final StubFile file;
        ProjectFixture(com.commonwealthrobotics.ActiveProject project, StubFile file) {
            this.project = project; this.file = file;
        }
    }
    public static void main(String[] args) throws Exception {
        Platform.setImplicitExit(false);
        Platform.startup(() -> { });
        try {
            runProbe();
            System.out.println("PASS: ModelEditQueue serial order, busy lifetime, failure recovery and FX responsiveness");
        } finally {
            Platform.exit();
        }
    }

    private static void runProbe() throws Exception {
        CountDownLatch firstStarted = new CountDownLatch(1);
        CountDownLatch releaseFirst = new CountDownLatch(1);
        CountDownLatch secondStarted = new CountDownLatch(1);
        CountDownLatch secondReturned = new CountDownLatch(1);
        CountDownLatch releaseSecond = new CountDownLatch(1);
        AtomicInteger order = new AtomicInteger();
        AtomicBoolean fxSubmitReturned = new AtomicBoolean();

        runFx(() -> {
            ModelEditQueue.submit(null, () -> {
                require(order.incrementAndGet() == 1, "first commit did not run first");
                firstStarted.countDown();
                await(releaseFirst);
            });
            ModelEditQueue.submit(null, () -> {
                require(order.incrementAndGet() == 2, "second commit ran before first completed");
                secondStarted.countDown();
                secondReturned.countDown();
                await(releaseSecond);
            });
            fxSubmitReturned.set(true);
        });
        require(fxSubmitReturned.get(), "FX submit callback did not return");
        require(firstStarted.await(LIMIT_MS, TimeUnit.MILLISECONDS), "first commit did not start");
        require(!secondStarted.await(150, TimeUnit.MILLISECONDS), "second commit bypassed serial queue");
        require(ModelEditQueue.isBusy(null), "queue stopped reporting busy while commits pending");

        releaseFirst.countDown();
        require(secondStarted.await(LIMIT_MS, TimeUnit.MILLISECONDS), "second commit did not start after first");
        require(secondReturned.await(100, TimeUnit.MILLISECONDS), "second commit did not reach controlled return");
        require(ModelEditQueue.isBusy(null), "busy cleared before second commit returned");
        releaseSecond.countDown();
        awaitIdle(null);

        CountDownLatch recovered = new CountDownLatch(1);
        runFx(() -> {
            ModelEditQueue.submit(null, () -> { throw new AssertionError("intentional commit failure"); });
            ModelEditQueue.submit(null, () -> recovered.countDown());
        });
        require(recovered.await(LIMIT_MS, TimeUnit.MILLISECONDS), "queue did not recover after failed commit");
            awaitIdle(null);

        checkExternalOperationGate();
    }

    private static void checkExternalOperationGate() throws Exception {
        ProjectFixture fixture = allocateProjectWithRunningOperation();
        com.commonwealthrobotics.ActiveProject project = fixture.project;
        require(project.get().isOperationRunning(), "Unsafe fixture did not expose a live operation");
        CountDownLatch ran = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        runFx(() -> ModelEditQueue.submit(project, () -> {
            ran.countDown();
            await(release);
        }));
        require(project.get().isOperationRunning(), "external operation ended before queue gate check");
        boolean startedEarly = ran.await(200, TimeUnit.MILLISECONDS);
        require(!startedEarly, "commit bypassed external operation gate");
        require(ModelEditQueue.isBusy(project), "busy was cleared while external operation was active");
        fixture.file.running = false;
        require(ran.await(LIMIT_MS, TimeUnit.MILLISECONDS), "commit did not start after external operation ended");
        release.countDown();
        awaitIdle(project);
    }

    private static ProjectFixture allocateProjectWithRunningOperation() throws Exception {
        Unsafe unsafe = getUnsafe();
        StubFile file = new StubFile(true);
        com.commonwealthrobotics.ActiveProject project =
                (com.commonwealthrobotics.ActiveProject) unsafe.allocateInstance(com.commonwealthrobotics.ActiveProject.class);
        Field fromFile = project.getClass().getDeclaredField("fromFile");
        fromFile.setAccessible(true);
        fromFile.set(project, file);
        return new ProjectFixture(project, file);
    }

    private static Unsafe getUnsafe() throws Exception {
        Field field = Unsafe.class.getDeclaredField("theUnsafe");
        field.setAccessible(true);
        return (Unsafe) field.get(null);
    }

    private static void runFx(Runnable action) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicBoolean failed = new AtomicBoolean();
        Platform.runLater(() -> {
            try { action.run(); } catch (Throwable ex) { ex.printStackTrace(); failed.set(true); } finally { done.countDown(); }
        });
        require(done.await(LIMIT_MS, TimeUnit.MILLISECONDS), "FX callback timed out");
        require(!failed.get(), "FX callback failed");
    }

    private static void awaitIdle(Object project) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(LIMIT_MS);
        while (ModelEditQueue.isBusy((com.commonwealthrobotics.ActiveProject) project)) {
            if (System.nanoTime() > deadline) throw new AssertionError("queue remained busy");
            Thread.sleep(10);
        }
    }

    private static void await(CountDownLatch latch) {
        try { require(latch.await(LIMIT_MS, TimeUnit.MILLISECONDS), "controlled commit timed out"); }
        catch (InterruptedException ex) { Thread.currentThread().interrupt(); throw new AssertionError(ex); }
    }

    private static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }
}
