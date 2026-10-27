package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import java.nio.file.*;
import javafx.animation.*;
import javafx.application.Platform;
import javafx.event.ActionEvent;
import javafx.scene.control.*;
import javafx.scene.control.ButtonBar.ButtonData;
import javafx.scene.layout.*;
import javafx.stage.Stage;
import javafx.util.Duration;

/** A visible workshop starts with a pupil/project choice; hidden prewarming never prompts. */
public final class ProjectStartDialog {
    private final Pane root;
    private final ActiveProject project;
    private Dialog<Void> dialog;
    private boolean requested = true, awaitingActivation, opening, leaving, namingCurrentModel;

    public static void install(Pane root, ActiveProject project) {
        ProjectStartDialog flow = new ProjectStartDialog(root, project);
        root.getProperties().put(ProjectStartDialog.class, flow);
    }
    public static void activate(Pane root) {
        if (root.getProperties().get(ProjectStartDialog.class) instanceof ProjectStartDialog flow) {
            flow.awaitingActivation = false; flow.requested = true; flow.poll();
        }
    }
    public static void afterFinish(Pane root) {
        if (root.getProperties().get(ProjectStartDialog.class) instanceof ProjectStartDialog flow) {
            flow.awaitingActivation = true; flow.requested = true;
        }
    }
    public static boolean editingAllowed(Pane root) {
        return !(root.getProperties().get(ProjectStartDialog.class) instanceof ProjectStartDialog flow)
            || (!flow.requested && !flow.awaitingActivation);
    }
    public static void openedExternally(Pane root) {
        if (root.getProperties().get(ProjectStartDialog.class) instanceof ProjectStartDialog flow) flow.accepted();
    }
    public static void nameCurrentModel(Pane root) {
        if (root.getProperties().get(ProjectStartDialog.class) instanceof ProjectStartDialog flow) {
            flow.namingCurrentModel = true; activate(root);
        }
    }
    public static void openProjects(Pane root) {
        dismissForFinish(root);
        ProjectCloseGuard.requestFinish(root, result -> {
            if (!"completed".equals(result)) return;
            try {
                Path directory = ProjectNaming.projectsRoot().resolve(".elevhub"); Files.createDirectories(directory);
                Path temporary = directory.resolve("cadoodle-projects.tmp");
                Files.writeString(temporary, "{\"created\":" + System.currentTimeMillis()/1000.0 + "}");
                Files.move(temporary, directory.resolve("cadoodle-projects.ready"),
                    StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } catch (java.io.IOException failure) {
                activate(root);
                Alert error = new Alert(Alert.AlertType.ERROR, "Dine projekter kunne ikke åbnes. Prøv igen.");
                error.initOwner(root.getScene().getWindow()); error.show();
            }
        });
    }
    public static void dismissForFinish(Pane root) {
        if (root.getProperties().get(ProjectStartDialog.class) instanceof ProjectStartDialog flow && flow.dialog != null) {
            flow.leaving = true; flow.dialog.close(); flow.dialog = null; flow.leaving = false;
        }
    }
    private ProjectStartDialog(Pane root, ActiveProject project) {
        this.root = root; this.project = project;
        root.setDisable(true);
        Timeline watch = new Timeline(new KeyFrame(Duration.millis(300), event -> poll()));
        watch.setCycleCount(Timeline.INDEFINITE); watch.play();
    }
    private void poll() {
        if (!requested || dialog != null || awaitingActivation || opening || BackgroundPrewarm.isWaiting()
                || project.isPreparingProjectFinish() || root.getScene() == null
                || !(root.getScene().getWindow() instanceof Stage stage) || !stage.isShowing()
                || stage.isIconified() || stage.getOpacity() == 0 || !project.isOpen()
                || project.get() == null || !project.get().isInitialized()) return;
        if (ProjectNaming.name(project.get()) != null) { requested = false; root.setDisable(false); return; }
        show(stage);
    }
    private void show(Stage stage) {
        root.setDisable(true);
        ButtonType start = new ButtonType("Start nyt projekt", ButtonData.OK_DONE);
        ButtonType open = new ButtonType("Åbn projekt", ButtonData.LEFT);
        dialog = new Dialog<>(); dialog.initOwner(stage); dialog.setTitle("Velkommen til 3D-værkstedet");
        dialog.setHeaderText("Start et nyt projekt eller åbn et, du har gemt");
        dialog.getDialogPane().getButtonTypes().addAll(start, open);
        // A cancel-type button lets programmatic close() work; user dismissal is
        // still guarded until a start/open choice has actually succeeded.
        dialog.getDialogPane().getButtonTypes().add(ButtonType.CANCEL);
        dialog.getDialogPane().lookupButton(ButtonType.CANCEL).setVisible(false);
        dialog.getDialogPane().lookupButton(ButtonType.CANCEL).setManaged(false);
        dialog.getDialogPane().setPrefWidth(560);
        ComboBox<String> schoolClass = new ComboBox<>(javafx.collections.FXCollections.observableArrayList(ProjectNaming.knownClasses()));
        schoolClass.setEditable(true); schoolClass.getEditor().setPromptText("Vælg eller skriv klasse");
        TextField firstName = new TextField(), name = new TextField(namingCurrentModel ? project.get().getMyProjectName() : "");
        firstName.setPromptText("Fornavn og evt. initial"); name.setPromptText("Projektets navn");
        GridPane form = new GridPane(); form.setHgap(12); form.setVgap(12);
        form.addRow(0, new Label("Klasse"), schoolClass); form.addRow(1, new Label("Fornavn"), firstName); form.addRow(2, new Label("Projekt"), name);
        Label status = new Label("Dit projekt bliver først gemt i Dine projekter, når du vælger Gem."); status.setWrapText(true);
        dialog.getDialogPane().setContent(new VBox(16, form, status));
        dialog.setOnCloseRequest(event -> { if (!leaving) event.consume(); });
        dialog.getDialogPane().lookupButton(start).addEventFilter(ActionEvent.ACTION, event -> {
            event.consume();
            try {
                var chosen = new ProjectNaming.Name(schoolClass.getEditor().getText(), firstName.getText(), name.getText());
                // A recovered nonempty model must pass the normal save/discard boundary first.
                if (!namingCurrentModel && !project.get().getOperations().isEmpty()) {
                    dismissForFinish(root);
                    ProjectCloseGuard.requestFinish(root, result -> {
                        if ("completed".equals(result)) { ProjectNaming.nameDraft(project.get(), chosen); accepted(); }
                        else activate(root);
                    });
                } else { ProjectNaming.nameDraft(project.get(), chosen); accepted(); }
            } catch (IllegalArgumentException invalid) { status.setText(invalid.getMessage()); }
        });
        dialog.getDialogPane().lookupButton(open).addEventFilter(ActionEvent.ACTION, event -> {
            event.consume(); openProjects(root);
        });
        dialog.show(); schoolClass.requestFocus();
    }
    private void accepted() {
        requested = false; awaitingActivation = false; opening = false;
        namingCurrentModel = false;
        leaving = true; if (dialog != null) dialog.close(); dialog = null; leaving = false;
        root.setDisable(false);
        if (root.lookup("#fileNameBox") instanceof TextField title) title.setText(project.get().getMyProjectName());
    }
}
