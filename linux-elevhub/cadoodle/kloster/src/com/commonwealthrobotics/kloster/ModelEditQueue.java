package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.neuronrobotics.sdk.common.Log;
import java.util.Map;
import java.util.WeakHashMap;
import java.util.concurrent.CompletableFuture;
import javafx.application.Platform;

/** Serial geometry commits; capture gesture data before submitting on the FX thread. */
public final class ModelEditQueue {
    private static final Map<ActiveProject, State> STATES = new WeakHashMap<>();
    private static final class State {
        int pending;
        CompletableFuture<Void> tail = CompletableFuture.completedFuture(null);
    }
    private ModelEditQueue() { }

    public static synchronized boolean isBusy(ActiveProject project) {
        State state = STATES.get(project);
        return state != null && state.pending > 0;
    }

    public static synchronized void submit(ActiveProject project, Runnable commit) {
        if (!Platform.isFxApplicationThread())
            throw new IllegalStateException("Capture model edits on the JavaFX thread");
        State state = STATES.computeIfAbsent(project, ignored -> new State());
        final var file = project == null ? null : project.get();
        state.pending++;
        state.tail = state.tail.thenRunAsync(() -> {
            // Native operations outside this queue (e.g. grouping) also need to
            // finish before a numeric edit reads the current geometry.
            while (file != null && file.isOperationRunning()) {
                if (project.get() != file) return;
                try { Thread.sleep(10); }
                catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    throw new java.util.concurrent.CompletionException(interrupted);
                }
            }
            commit.run();
        }).handle((ignored, error) -> {
            if (error != null) Log.error(error);
            Platform.runLater(() -> finished(project, state));
            return null;
        });
    }

    private static synchronized void finished(ActiveProject project, State state) {
        if (--state.pending == 0 && STATES.get(project) == state) STATES.remove(project);
    }
}
