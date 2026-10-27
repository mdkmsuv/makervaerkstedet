package com.commonwealthrobotics.kloster;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.regex.Pattern;

import javafx.application.Platform;
import javafx.event.EventHandler;
import javafx.stage.Stage;
import javafx.stage.Window;
import javafx.stage.WindowEvent;

/** Optional launcher bridge for loading CaDoodle before the pupil opens it. */
public final class BackgroundPrewarm {
    private static final Pattern NONCE = Pattern.compile("[0-9a-f]{32}");
    private static final String ENV = "ELEVHUB_CADOODLE_PREWARM";
    private static volatile boolean prewarming = isValidNonce(System.getenv(ENV));
    private static BackgroundPrewarm installed;
    private static final String INSTALL_KEY = BackgroundPrewarm.class.getName();

    private final Stage primary;
    private final Path directory;
    private final Path ready;
    private final Path show;
    private final String nonce;
    private final AtomicBoolean waiting = new AtomicBoolean(true);
    private final AtomicBoolean fxTaskPending = new AtomicBoolean(false);
    private final List<Window> hiddenWindows = new ArrayList<>();
    private final boolean originalIconified;
    private final double originalOpacity;
    private final boolean originalImplicitExit;
    private EventHandler<WindowEvent> shownHandler;
    private EventHandler<WindowEvent> closeHandler;
    private boolean modelReady;
    private boolean hidden;
    private final ScheduledExecutorService watcher = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread thread = new Thread(r, "cadoodle-prewarm-watcher");
        thread.setDaemon(true);
        return thread;
    });

    private BackgroundPrewarm(Stage primary, String nonce, Path directory) {
        this.primary = primary;
        this.nonce = nonce;
        this.directory = directory;
        this.ready = directory.resolve(nonce + ".ready");
        this.show = directory.resolve(nonce + ".show");
        this.originalIconified = primary.isIconified();
        this.originalOpacity = primary.getOpacity();
        this.originalImplicitExit = Platform.isImplicitExit();
    }

    /** Installs the bridge when the launcher has explicitly enabled it. */
    public static void install(Stage primary) {
        if (!prewarming || primary == null || primary.getProperties().containsKey(INSTALL_KEY)) return;
        String nonce = System.getenv(ENV);
        if (!isValidNonce(nonce)) return;
        try {
            Path directory = Path.of(System.getProperty("user.home"), ".local", "state", "elevhub", "prewarm");
            Files.createDirectories(directory);
            installed = new BackgroundPrewarm(primary, nonce, directory);
            primary.getProperties().put(INSTALL_KEY, installed);
            installed.start();
        } catch (RuntimeException | IOException ignored) {
            prewarming = false;
            // Prewarming is an optional startup optimisation; normal startup must continue.
        }
    }

    public static boolean isWaiting() { return prewarming; }

    public static void modelReady() {
        if (installed != null && installed.waiting.get()) {
            installed.modelReady = true;
            if (installed.hidden && !installed.writeReady()) installed.finish(true);
        }
    }

    static boolean isValidNonce(String nonce) {
        return nonce != null && NONCE.matcher(nonce).matches();
    }

    static BackgroundPrewarm installForTest(Stage primary, String nonce, Path directory) throws IOException {
        Files.createDirectories(directory);
        BackgroundPrewarm bridge = new BackgroundPrewarm(primary, nonce, directory);
        bridge.modelReady = true;
        bridge.start();
        return bridge;
    }

    private void start() {
        try {
            Files.deleteIfExists(ready);
            Files.deleteIfExists(show);
            for (Window window : Window.getWindows()) {
                if (window != primary && window.isShowing()) hiddenWindows.add(window);
            }
            primary.setOpacity(0.0);
            Platform.setImplicitExit(false);
            shownHandler = event -> primaryShown();
            closeHandler = event -> { if (waiting.get()) finish(false); };
            primary.addEventHandler(WindowEvent.WINDOW_SHOWN, shownHandler);
            primary.addEventHandler(WindowEvent.WINDOW_CLOSE_REQUEST, closeHandler);
            watcher.scheduleWithFixedDelay(this::poll, 40, 80, TimeUnit.MILLISECONDS);
        } catch (RuntimeException | IOException ignored) {
            finish(false);
        }
    }

    private void primaryShown() {
        if (!waiting.get()) return;
        // Defer until the native peer exists: some window managers throw when
        // iconified is set before the first show. Opacity remains the guard.
        Platform.runLater(() -> {
            if (!waiting.get()) return;
            try { primary.setIconified(true); } catch (RuntimeException ignored) { }
            primary.hide();
            hidden = true;
            if (modelReady && !writeReady()) finish(true);
        });
    }

    private void poll() {
        if (!waiting.get()) return;
        if (Files.exists(show)) {
            if (fxTaskPending.compareAndSet(false, true)) {
                Platform.runLater(() -> { fxTaskPending.set(false); activate(); });
            }
            return;
        }
        if (fxTaskPending.compareAndSet(false, true)) {
            Platform.runLater(() -> { fxTaskPending.set(false); hideOtherWindows(); });
        }
    }

    private boolean writeReady() {
        try {
            String json = "{\"pid\":" + ProcessHandle.current().pid() + ",\"nonce\":\"" + nonce + "\"}";
            Path temporary = directory.resolve(ready.getFileName() + ".tmp");
            Files.writeString(temporary, json, StandardCharsets.UTF_8);
            try {
                Files.move(temporary, ready, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
            } catch (java.nio.file.AtomicMoveNotSupportedException unsupported) {
                Files.move(temporary, ready, StandardCopyOption.REPLACE_EXISTING);
            }
            return true;
        } catch (IOException ignored) {
            return false;
        }
    }

    private void hideOtherWindows() {
        if (!waiting.get()) return;
        for (Window window : List.copyOf(Window.getWindows())) {
            if (window != primary && window.isShowing()) {
                if (!hiddenWindows.contains(window)) hiddenWindows.add(window);
                window.hide();
            }
        }
    }

    private void activate() {
        if (!showMarkerMatches()) return;
        if (!waiting.compareAndSet(true, false)) return;
        prewarming = false;
        watcher.shutdownNow();
        try { Files.deleteIfExists(ready); } catch (IOException ignored) { }
        try { Files.deleteIfExists(show); } catch (IOException ignored) { }
        primary.removeEventHandler(WindowEvent.WINDOW_SHOWN, shownHandler);
        primary.removeEventHandler(WindowEvent.WINDOW_CLOSE_REQUEST, closeHandler);
        primary.setOpacity(originalOpacity);
        primary.setIconified(originalIconified);
        if (!primary.isShowing()) primary.show();
        restoreHiddenWindows();
        try { Files.deleteIfExists(show); } catch (IOException ignored) { }
        primary.toFront();
        primary.requestFocus();
        Platform.setImplicitExit(originalImplicitExit);
    }

    private boolean showMarkerMatches() {
        try { return Files.readString(show, StandardCharsets.UTF_8).trim().equals(nonce); }
        catch (IOException ignored) { return false; }
    }

    private void finish(boolean restore) {
        if (!waiting.compareAndSet(true, false)) return;
        prewarming = false;
        watcher.shutdownNow();
        try { Files.deleteIfExists(ready); } catch (IOException ignored) { }
        try { Files.deleteIfExists(show); } catch (IOException ignored) { }
        if (shownHandler != null) primary.removeEventHandler(WindowEvent.WINDOW_SHOWN, shownHandler);
        if (closeHandler != null) primary.removeEventHandler(WindowEvent.WINDOW_CLOSE_REQUEST, closeHandler);
        primary.setOpacity(originalOpacity);
        primary.setIconified(originalIconified);
        Platform.setImplicitExit(originalImplicitExit);
        if (restore && !primary.isShowing()) primary.show();
        if (restore) restoreHiddenWindows();
    }

    private void restoreHiddenWindows() {
        for (Window window : hiddenWindows) {
            if (window instanceof Stage stage && !stage.isShowing()) stage.show();
        }
    }
}
