package com.commonwealthrobotics.kloster;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import javafx.application.Platform;
import javafx.event.EventHandler;
import javafx.scene.Scene;
import javafx.scene.layout.Pane;
import javafx.stage.Stage;
import javafx.stage.WindowEvent;

/** Private JavaFX lifecycle probe; run under xvfb-run, never against the user display. */
public final class BackgroundPrewarmLifecycleProbe {
    private static final String NONCE = "0123456789abcdef0123456789abcdef";
    private static final AtomicReference<Throwable> FAILURE = new AtomicReference<>();

    public static void main(String[] args) throws Exception {
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> FAILURE.compareAndSet(null, error));
        Platform.startup(() -> { });
        Path directory = Files.createTempDirectory("cadoodle-prewarm-probe-");
        CountDownLatch shownTwice = new CountDownLatch(2);
        AtomicInteger closeCalls = new AtomicInteger();
        Stage[] stages = new Stage[2];
        try {
            fx(() -> {
                Stage primary = new Stage();
                primary.setScene(new Scene(new Pane(), 120, 80));
                primary.addEventHandler(WindowEvent.WINDOW_SHOWN, event -> shownTwice.countDown());
                primary.addEventHandler(WindowEvent.WINDOW_CLOSE_REQUEST, event -> closeCalls.incrementAndGet());
                Stage secondary = new Stage();
                secondary.setScene(new Scene(new Pane(), 80, 60));
                secondary.show();
                try {
                    BackgroundPrewarm.installForTest(primary, NONCE, directory);
                } catch (java.io.IOException error) {
                    throw new RuntimeException(error);
                }
                stages[0] = primary; stages[1] = secondary;
                primary.show();
            });
            waitFor(directory.resolve(NONCE + ".ready"));
            String ready = Files.readString(directory.resolve(NONCE + ".ready"), StandardCharsets.UTF_8);
            require(ready.contains("\"nonce\":\"" + NONCE + "\""), "ready nonce missing");
            Files.writeString(directory.resolve(NONCE + ".show"), "wrongnonce");
            Thread.sleep(220);
            fx(() -> require(!stages[0].isShowing(), "invalid marker activated primary"));
            CountDownLatch blockEntered = new CountDownLatch(1);
            CountDownLatch releaseFx = new CountDownLatch(1);
            fx(() -> Platform.runLater(() -> {
                blockEntered.countDown();
                try { require(releaseFx.await(5, TimeUnit.SECONDS), "FX hold timed out"); }
                catch (InterruptedException error) { throw new RuntimeException(error); }
            }));
            require(blockEntered.await(2, TimeUnit.SECONDS), "FX hold did not start");
            Files.writeString(directory.resolve(NONCE + ".show"), NONCE);
            Thread.sleep(220);
            Files.deleteIfExists(directory.resolve(NONCE + ".show"));
            releaseFx.countDown();
            Thread.sleep(220);
            fx(() -> require(!stages[0].isShowing(), "queued cancelled marker activated primary"));
            Files.writeString(directory.resolve(NONCE + ".show"), NONCE);
            require(shownTwice.await(5, TimeUnit.SECONDS), "manual show did not activate primary");
            fx(() -> {
                require(stages[0].isShowing(), "primary hidden after activation");
                require(stages[1].isShowing(), "secondary stage was not restored");
                stages[0].fireEvent(new WindowEvent(stages[0], WindowEvent.WINDOW_CLOSE_REQUEST));
            });
            require(closeCalls.get() == 1, "existing close handler was replaced");
            require(!Files.exists(directory.resolve(NONCE + ".ready")), "ready marker remained");
            require(!Files.exists(directory.resolve(NONCE + ".show")), "show marker remained");
            Throwable failure = FAILURE.get();
            if (failure != null) throw new AssertionError("unexpected JavaFX thread failure", failure);
            System.out.println("PASS: BackgroundPrewarm private lifecycle, marker validation, handler preservation, and secondary restore");
        } finally {
            fx(() -> { for (Stage stage : stages) if (stage != null) stage.close(); });
            Platform.exit();
        }
    }

    private static void waitFor(Path path) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (!Files.exists(path) && System.nanoTime() < deadline) Thread.sleep(20);
        require(Files.exists(path), "ready marker missing");
    }

    private static void fx(Action action) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        Platform.runLater(() -> {
            try { action.run(); }
            catch (Throwable error) { FAILURE.compareAndSet(null, error); }
            finally { done.countDown(); }
        });
        require(done.await(5, TimeUnit.SECONDS), "FX action timed out");
        Throwable failure = FAILURE.get();
        if (failure != null) throw new AssertionError("FX action failed", failure);
    }

    private interface Action { void run(); }
    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
