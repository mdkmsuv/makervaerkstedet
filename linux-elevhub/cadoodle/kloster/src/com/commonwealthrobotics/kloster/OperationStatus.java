package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import java.util.Map;
import java.util.WeakHashMap;
import java.util.concurrent.CompletableFuture;
import javafx.application.Platform;
import javafx.scene.control.Label;
import javafx.scene.control.ProgressIndicator;

/** Non-modal progress for alignment. Never waits for geometry on the FX thread. */
public final class OperationStatus {
    private static final Map<ActiveProject, State> STATES = new WeakHashMap<>();
    private static final class State {
        int pending;
        Label label;
        String message = "";
    }
    private OperationStatus() { }

    public static void install(ActiveProject project, Label label) {
        if (!Platform.isFxApplicationThread()) throw new IllegalStateException("FX thread required");
        ProgressIndicator spinner = new ProgressIndicator();
        spinner.setPrefSize(16, 16);
        spinner.setMaxSize(16, 16);
        label.setGraphic(spinner);
        label.setMinWidth(0);
        label.managedProperty().bind(label.visibleProperty());
        synchronized (STATES) {
            State state = STATES.computeIfAbsent(project, ignored -> new State());
            state.label = label;
            render(state);
        }
    }

    public static void watch(ActiveProject project, Thread operation, String message) {
        if (operation == null) return;
        final State state;
        synchronized (STATES) {
            state = STATES.computeIfAbsent(project, ignored -> new State());
            state.pending++;
            state.message = message;
        }
        Platform.runLater(() -> render(state));
        CompletableFuture.runAsync(() -> {
            try { operation.join(); }
            catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
            finally {
                synchronized (STATES) { state.pending--; }
                Platform.runLater(() -> render(state));
            }
        });
    }

    private static void render(State state) {
        synchronized (STATES) {
            if (state.label == null) return;
            state.label.setText(state.pending == 0 ? "" : state.message);
            state.label.setVisible(state.pending > 0);
        }
    }
}
