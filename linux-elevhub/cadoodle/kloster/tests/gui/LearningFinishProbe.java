package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.nio.file.*;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.function.BooleanSupplier;
import javafx.application.Platform;
import javafx.scene.Scene;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.stage.*;

/** Learning-template replacement must cross the real finish dialog and keep pupil identity. */
public final class LearningFinishProbe {
    private static final class Blank extends CaDoodleFile { @Override public boolean isInitialized() { return true; } }
    private static final class Project extends ActiveProject {
        volatile CaDoodleFile current;
        volatile boolean dirty = true;
        volatile int resets;
        Project() throws Exception { current = blank("original"); ProjectNaming.nameDraft(current, new ProjectNaming.Name("6.B", "Maja", "Min gamle figur")); }
        private CaDoodleFile blank(String name) throws Exception {
            Path folder = ProjectNaming.workingDirectory().toPath().resolve(name); Files.createDirectories(folder);
            CaDoodleFile file = new Blank().setSelf(folder.resolve("Model.doodle").toFile());
            Files.writeString(file.getSelf().toPath(), file.toJson()); return file;
        }
        @Override public CaDoodleFile get() { return current; }
        @Override public boolean hasUnsavedChanges() { return dirty; }
        @Override public boolean prepareProjectClose(boolean save) { if (save) throw new AssertionError("discard test unexpectedly saved"); return true; }
        @Override public void resetProjectAfterFinish() throws Exception { current = blank("new-" + (++resets)); dirty = false; }
        @Override public CaDoodleFile setActiveProject(java.io.File path) throws Exception { current.close(); current = CaDoodleFile.fromFile(path, null, false); return current; }
    }
    private static Stage owner;
    private static Pane root;
    private static Project project;
    private static LearningLibrary library;
    private static Dialog<Void> guide;
    public static void main(String[] args) throws Exception {
        System.setProperty("elevhub.projects", Files.createTempDirectory("learning-finish-").resolve("Projekter").toString());
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        Platform.startup(() -> Platform.setImplicitExit(false));
        try {
            fx(() -> { try {
                project = new Project(); root = new Pane(); owner = new Stage(); ProjectCloseGuard.install(root, project);
                owner.setScene(new Scene(root, 700, 500)); owner.show(); library = new LearningLibrary(project, root);
            } catch (Exception failure) { throw new RuntimeException(failure); } });
            CaDoodleFile original = project.get();
            fx(LearningFinishProbe::start);
            fx(() -> choose("Arbejd videre"));
            require(project.get() == original && project.resets == 0, "cancelled guide replacement lost current model");
            fx(guide::close);
            fx(LearningFinishProbe::start); fx(() -> choose("Afslut uden at gemme"));
            await(() -> project.get() != original && !project.get().getOperations().isEmpty()
                && ProjectNaming.name(project.get()) != null);
            ProjectNaming.Name pupil = ProjectNaming.name(project.get());
            require(pupil.schoolClass().equals("6.B") && pupil.firstName().equals("Maja"), "template lost class/pupil identity");
            require(!pupil.projectName().equals("Min gamle figur"), "template reused previous project title");
            require(project.resets == 1 && ProjectNaming.hasUnpublishedChanges(project.get()), "new template must need its own explicit save");
            try (var folders = Files.list(ProjectNaming.projectsRoot())) {
                require(folders.allMatch(path -> path.getFileName().toString().startsWith(".")), "discard/template creation published an unrequested snapshot");
            }
            System.out.println("PASS: learning-template cancel/discard boundary, inherited class/pupil, new title and unpublished starter");
        } finally { fx(() -> { if (guide != null) guide.close(); if (owner != null) owner.hide(); }); Platform.exit(); }
    }
    private static void start() {
        try {
            guide = new Dialog<>(); guide.initOwner(owner); guide.getDialogPane().getButtonTypes().add(ButtonType.CANCEL);
            VBox content = new VBox(); Label status = new Label(); guide.getDialogPane().setContent(content); guide.show();
            var method = LearningLibrary.class.getDeclaredMethod("start", Dialog.class, VBox.class, LearningCatalog.Entry.class, String.class, Label.class);
            method.setAccessible(true); method.invoke(library, guide, content, LearningCatalog.find("name-tag"), "NY", status);
        } catch (Exception failure) { throw new RuntimeException(failure); }
    }
    private static void choose(String label) {
        for (Window window : List.copyOf(Window.getWindows())) if (window.isShowing() && window.getScene().getRoot() instanceof DialogPane pane)
            for (ButtonType type : pane.getButtonTypes()) if (type.getText().equals(label)) { ((Button) pane.lookupButton(type)).fire(); return; }
        throw new AssertionError("missing finish choice " + label);
    }
    private static void fx(Runnable action) throws Exception { CompletableFuture<Void> done = new CompletableFuture<>(); Platform.runLater(() -> { try { action.run(); done.complete(null); } catch (Throwable failure) { done.completeExceptionally(failure); } }); done.get(10, TimeUnit.SECONDS); }
    private static void await(BooleanSupplier ready) throws Exception { long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(75); while (!ready.getAsBoolean()) { if (System.nanoTime() > end) throw new AssertionError("template test timed out"); Thread.sleep(50); } }
    private static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
}
