import com.commonwealthrobotics.align.AlignHandle;
import com.commonwealthrobotics.kloster.OperationStatus;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Align;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Alignment;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.Vector3d;
import javafx.application.Platform;
import javafx.scene.control.Label;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.transform.Affine;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

/** No Stage: native align click ordering plus concurrent non-modal progress. */
public final class AlignmentInputProbe {
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> {});
        try {
            fx(() -> {
                Align operation = new Align();
                AlignHandle handle = new AlignHandle(Alignment.positive, new Affine(),
                    new Affine(), Vector3d.xyz(1, 0, 0), null);
                handle.getHandle();
                handle.initialize(operation, null,
                    List.of(new Cube(10).toCSG(), new Cube(10).toCSG().movex(20)), List.of("a", "b"));
                handle.setOnClickCallback(() -> require(operation.x == Alignment.positive,
                    "chosen X alignment must be set before commit dispatch"));
                // Use the actual handler, independently of queued node-show calls.
                try {
                    var field = AlignHandle.class.getDeclaredField("onClickEvent");
                    field.setAccessible(true);
                    @SuppressWarnings("unchecked") var click =
                        (javafx.event.EventHandler<MouseEvent>) field.get(handle);
                    click.handle(new MouseEvent(MouseEvent.MOUSE_CLICKED, 0, 0, 0, 0,
                        MouseButton.PRIMARY, 1, false, false, false, false,
                        false, false, false, true, false, false, null));
                } catch (ReflectiveOperationException error) { throw new AssertionError(error); }
            });
            Label[] label = new Label[1];
            fx(() -> { label[0] = new Label(); OperationStatus.install(null, label[0]); });
            CountDownLatch firstDone = new CountDownLatch(1), secondDone = new CountDownLatch(1);
            Thread first = waiting(firstDone), second = waiting(secondDone);
            OperationStatus.watch(null, first, "Justerer figurer…");
            OperationStatus.watch(null, second, "Justerer figurer…");
            fx(() -> require(label[0].isVisible(), "status must be visible without blocking FX"));
            firstDone.countDown(); first.join();
            fx(() -> require(label[0].isVisible(), "first completion must not clear a second operation"));
            secondDone.countDown(); second.join();
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
            boolean[] visible = {true};
            do {
                fx(() -> visible[0] = label[0].isVisible());
                if (System.nanoTime() > deadline) throw new AssertionError("status never cleared");
                Thread.sleep(10);
            } while (visible[0]);
            System.out.println("PASS: alignment input captured before callback; inline concurrent progress and responsive FX");
        } finally { Platform.exit(); }
    }
    private static Thread waiting(CountDownLatch latch) {
        Thread thread = new Thread(() -> {
            try { if (!latch.await(6, TimeUnit.SECONDS)) throw new AssertionError("test timeout"); }
            catch (InterruptedException error) { throw new AssertionError(error); }
        });
        thread.setDaemon(true); thread.start(); return thread;
    }
    private static void fx(Runnable action) throws Exception {
        FutureTask<Void> task = new FutureTask<>(action, null);
        Platform.runLater(task); task.get(30, TimeUnit.SECONDS);
    }
    private static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
}
