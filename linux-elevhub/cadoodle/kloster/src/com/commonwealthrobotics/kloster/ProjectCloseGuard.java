package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import java.util.function.Consumer;
import javafx.application.Platform;
import javafx.event.ActionEvent;
import javafx.event.Event;
import javafx.scene.Scene;
import javafx.scene.control.*;
import javafx.scene.control.ButtonBar.ButtonData;
import javafx.scene.layout.*;
import javafx.stage.Window;
import javafx.stage.WindowEvent;

/** One finish boundary for window close, the toolbar and the kiosk's asynchronous request. */
public final class ProjectCloseGuard {
    private final ActiveProject project;
    private final Window owner;
    private final Pane root;
    private Alert dialog;
    private boolean preparing, allowClose, finishing, completing, leaveAfterSaving = true;
    private Consumer<String> completion;
    private Runnable afterReset = () -> { };
    private ComboBox<String> schoolClass;
    private TextField firstName, projectName;

    public static void install(Pane root, ActiveProject project) {
        root.sceneProperty().addListener((ignored, before, scene) -> watchScene(root, scene, project));
        watchScene(root, root.getScene(), project);
    }
    private static void watchScene(Pane root, Scene scene, ActiveProject project) {
        if (scene == null) return;
        scene.windowProperty().addListener((ignored, before, window) -> attach(root, window, project));
        attach(root, scene.getWindow(), project);
    }
    private static void attach(Pane root, Window window, ActiveProject project) {
        if (window == null || window.getProperties().containsKey(ProjectCloseGuard.class)) return;
        window.getProperties().put(ProjectCloseGuard.class, new ProjectCloseGuard(project, window, root));
    }
    public static void requestFinish(Pane root, Consumer<String> completion) {
        request(root, true, completion);
    }
    public static void requestSave(Pane root) { request(root, false, result -> { }); }
    private static void request(Pane root, boolean leave, Consumer<String> completion) {
        if (!Platform.isFxApplicationThread()) { Platform.runLater(() -> request(root, leave, completion)); return; }
        if (root.getScene() == null || root.getScene().getWindow() == null) { completion.accept("error"); return; }
        Object guard = root.getScene().getWindow().getProperties().get(ProjectCloseGuard.class);
        if (!(guard instanceof ProjectCloseGuard installed)) { completion.accept("error"); return; }
        installed.begin(leave, completion);
    }
    public static void afterReset(Pane root, Runnable action) { root.getProperties().put("elevhub.afterReset", action); }
    private ProjectCloseGuard(ActiveProject project, Window owner, Pane root) {
        this.project = project; this.owner = owner; this.root = root;
        owner.addEventFilter(WindowEvent.WINDOW_CLOSE_REQUEST, event -> {
            if (allowClose) return;
            event.consume();
            if (finishing) return;
            begin(true, result -> {
                if (!"completed".equals(result)) return;
                allowClose = true;
                Event.fireEvent(owner, new WindowEvent(owner, WindowEvent.WINDOW_CLOSE_REQUEST));
            });
        });
    }
    private void begin(boolean leave, Consumer<String> callback) {
        if (finishing) { callback.accept("error"); return; }
        finishing = true; completing = false; completion = callback;
        leaveAfterSaving = leave;
        schoolClass = null; firstName = null; projectName = null;
        ProjectStartDialog.dismissForFinish(root);
        Object action = root.getProperties().get("elevhub.afterReset");
        afterReset = action instanceof Runnable run ? run : () -> { };
        project.pauseAutomaticSavingForClose(); root.setDisable(true);
        if (root.lookup("#fileNameBox") instanceof TextField title && project.get() != null)
            project.get().setProjectName(title.getText());
        if (!leave && ProjectNaming.name(project.get()) != null) prepare(true);
        else if (!leave || project.hasUnsavedChanges() || ProjectNaming.hasUnpublishedChanges(project.get())) showQuestion();
        else prepare(false);
    }
    private void showQuestion() {
        ButtonType save = new ButtonType(leaveAfterSaving ? "Gem og afslut" : "Gem projekt", ButtonData.YES);
        ButtonType discard = new ButtonType("Afslut uden at gemme", ButtonData.NO);
        ButtonType cancel = new ButtonType("Arbejd videre", ButtonData.CANCEL_CLOSE);
        dialog = new Alert(Alert.AlertType.CONFIRMATION,
            "Gem dit projekt lokalt, så du kan arbejde videre en anden gang.", save, discard, cancel);
        if (!leaveAfterSaving) dialog.getDialogPane().getButtonTypes().remove(discard);
        dialog.initOwner(owner); dialog.setTitle("Afslut projekt");
        dialog.setHeaderText(leaveAfterSaving ? "Vil du gemme, før du afslutter?" : "Gem projektet lokalt");
        dialog.setResizable(true); dialog.getDialogPane().setPrefWidth(620);
        dialog.getDialogPane().getStyleClass().add("kloster-dialog");
        if (owner.getScene() != null) dialog.getDialogPane().getStylesheets().addAll(owner.getScene().getStylesheets());
        schoolClass = null; firstName = null; projectName = null;
        if (ProjectNaming.name(project.get()) == null) {
            schoolClass = new ComboBox<>(javafx.collections.FXCollections.observableArrayList(ProjectNaming.knownClasses()));
            schoolClass.setEditable(true); schoolClass.getEditor().setPromptText("Vælg eller skriv klasse");
            firstName = new TextField(); firstName.setPromptText("Fornavn og evt. initial");
            projectName = new TextField(project.get().getMyProjectName());
            GridPane fields = new GridPane(); fields.setHgap(12); fields.setVgap(10);
            fields.addRow(0, new Label("Klasse"), schoolClass);
            fields.addRow(1, new Label("Fornavn"), firstName);
            fields.addRow(2, new Label("Projekt"), projectName);
            dialog.getDialogPane().setContent(new VBox(12,
                new Label("Skriv klasse, fornavn og projektnavn, når du gemmer."), fields));
        }
        Button saveButton = (Button) dialog.getDialogPane().lookupButton(save);
        Button discardButton = (Button) dialog.getDialogPane().lookupButton(discard);
        Button cancelButton = (Button) dialog.getDialogPane().lookupButton(cancel);
        saveButton.getStyleClass().add("kloster-primary");
        saveButton.setDefaultButton(false); if (discardButton != null) discardButton.setDefaultButton(false); cancelButton.setDefaultButton(true);
        saveButton.addEventFilter(ActionEvent.ACTION, event -> { event.consume(); prepare(true); });
        if (discardButton != null) discardButton.addEventFilter(ActionEvent.ACTION, event -> { event.consume(); prepare(false); });
        dialog.setOnCloseRequest(event -> { if (preparing) event.consume(); });
        dialog.setOnShown(event -> dialog.getDialogPane().getScene().getWindow()
            .addEventFilter(WindowEvent.WINDOW_CLOSE_REQUEST, close -> { if (preparing) close.consume(); }));
        dialog.setOnHidden(event -> { dialog = null; if (!completing) finish("cancelled"); });
        dialog.show(); cancelButton.requestFocus();
    }
    private void prepare(boolean save) {
        if (preparing) return;
        ProjectNaming.Name name = null;
        if (save) {
            try {
                name = schoolClass == null ? ProjectNaming.name(project.get()) :
                    new ProjectNaming.Name(schoolClass.getEditor().getText(), firstName.getText(), projectName.getText());
                if (name == null) throw new IllegalArgumentException("Skriv klasse, fornavn og projektnavn.");
                ProjectNaming.nameDraft(project.get(), name);
            } catch (IllegalArgumentException failure) { failure(failure.getMessage()); return; }
        }
        final ProjectNaming.Name finalName = name;
        preparing = true;
        if (dialog != null) {
            dialog.setHeaderText(save ? "Gemmer projektet …" : "Afslutter projektet …");
            dialog.getDialogPane().setDisable(true);
        }
        Thread worker = new Thread(() -> {
            try {
                var finishingFile = project.get();
                // Model completion calls back into synchronized ActiveProject methods.
                // Never hold that monitor while waiting for those model workers.
                if (!project.prepareProjectClose(save)) throw new java.io.IOException("Projektet er ikke klart endnu.");
                synchronized (project) {
                    if (project.get() != finishingFile) throw new java.io.IOException("Projektet blev skiftet. Prøv igen.");
                    if (save) {
                        ProjectNaming.publish(project.get(), finalName);
                        if (project.hasUnsavedChanges() || ProjectNaming.hasUnpublishedChanges(project.get()))
                            throw new java.io.IOException("Der kom nye ændringer under gemningen. Prøv igen.");
                    }
                    if (leaveAfterSaving) project.resetProjectAfterFinish();
                }
                Platform.runLater(() -> {
                    preparing = false; completing = true;
                    if (dialog != null) dialog.close();
                    if (leaveAfterSaving) { afterReset.run(); ProjectStartDialog.afterFinish(root); }
                    if (save && !leaveAfterSaving) {
                        Alert saved = new Alert(Alert.AlertType.INFORMATION,
                            "Projektet er gemt lokalt under " + finalName.folder() + ".", new ButtonType("Arbejd videre", ButtonData.OK_DONE));
                        saved.initOwner(owner); saved.setTitle("Projektet er gemt"); saved.setHeaderText("Gemt: " + finalName.projectName());
                        saved.setOnHidden(event -> finish("completed")); saved.show();
                    } else finish("completed");
                });
            } catch (Throwable error) {
                com.neuronrobotics.sdk.common.Log.error(error);
                Platform.runLater(() -> { preparing = false; failure("Projektet er stadig åbent. " +
                    (error.getMessage() == null ? "Gemningen lykkedes ikke. Prøv igen." : error.getMessage())); });
            }
        }, "CaDoodle-finish-project");
        worker.setDaemon(true); worker.start();
    }
    private void finish(String result) {
        project.cancelClosePreparation(); root.setDisable(!ProjectStartDialog.editingAllowed(root));
        finishing = false; preparing = false;
        Consumer<String> callback = completion; completion = null;
        if (callback != null) callback.accept(result);
    }
    private void failure(String message) {
        if (dialog == null) showQuestion();
        dialog.setHeaderText(message); dialog.getDialogPane().setDisable(false);
    }
}
