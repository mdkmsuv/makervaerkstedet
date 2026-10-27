package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.SaveOverwriteException;
import java.io.File;
import java.util.UUID;
import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.application.Platform;
import javafx.geometry.Insets;
import javafx.scene.Node;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.util.Duration;

/** Optional learning paths with real editable starters and help outside the 3D model. */
final class LearningLibrary {
    private final ActiveProject ap;
    private final Pane root;
    private final Button button = new Button("Find et projekt");
    private final TitledPane guidance = new TitledPane();
    private CaDoodleFile displayed;

    LearningLibrary(ActiveProject ap, Pane root) {
        this.ap = ap; this.root = root;
        button.setId("kloster-learning-library");
        button.getStyleClass().add("kloster-header-button");
        button.setOnAction(event -> show());
        guidance.setId("kloster-project-guide");
        guidance.getStyleClass().add("kloster-project-guide");
        guidance.setVisible(false); guidance.managedProperty().bind(guidance.visibleProperty());
        guidance.setMinHeight(0);
        guidance.maxHeightProperty().bind(javafx.beans.binding.Bindings.min(380, root.heightProperty().multiply(.46)));
        Timeline observe = new Timeline(new KeyFrame(Duration.seconds(1), event -> {
            CaDoodleFile current = ap.get();
            if (current != null && current.isInitialized() && current != displayed) {
                displayed = current;
                try {
                    String id = LearningProjectService.starterId(current);
                    if (id == null) guidance.setVisible(false);
                    else if (LearningProjectService.hasStepGuide(current)) showGuidance(LearningCatalog.find(id));
                    else showLegacyGuidance();
                } catch (java.io.IOException | IllegalArgumentException staleMetadata) {
                    guidance.setVisible(false);
                }
            }
        }));
        observe.setCycleCount(Timeline.INDEFINITE); observe.play();
        root.sceneProperty().addListener((source, before, after) -> {
            if (after == null) observe.stop(); else observe.play();
        });
    }

    Button button() { return button; }
    Node guidance() { return guidance; }

    private static Label text(String value, String style) {
        Label label = new Label(value); label.setWrapText(true); label.setMaxWidth(Double.MAX_VALUE);
        if (style != null) label.getStyleClass().add(style);
        return label;
    }

    private void showGuidance(LearningCatalog.Entry entry) {
        guidance.setText(entry.title() + " · trin for trin");
        File file = ap.get() == null ? null : ap.get().getSelf();
        guidance.setContent(new LearningGuidePane(entry, file == null ? null : file.toPath(),
            this::highlight, () -> guidance.setExpanded(false)));
        guidance.setVisible(true); guidance.setExpanded(true);
    }

    private void showLegacyGuidance() {
        guidance.setText("Din tidligere projektstarter");
        Button choose = new Button("Find et trin for trin-projekt"); choose.setWrapText(true);
        choose.setOnAction(event -> show());
        VBox content = new VBox(9, text("Fortsæt med din model, eller begynd et nyt forløb med løse dele og en guide.", null), choose);
        content.setPadding(new Insets(8)); guidance.setContent(content);
        guidance.setVisible(true); guidance.setExpanded(false);
    }

    private void highlight(java.util.List<String> tools) {
        java.util.List<Node> marked = new java.util.ArrayList<>();
        for (String id : tools) {
            Node tool = root.lookup("#" + id);
            if (tool != null && !tool.getStyleClass().contains("kloster-learning-highlight")) {
                tool.getStyleClass().add("kloster-learning-highlight"); marked.add(tool);
            }
        }
        new Timeline(new KeyFrame(Duration.seconds(7), event -> marked.forEach(
            tool -> tool.getStyleClass().remove("kloster-learning-highlight")))).play();
    }

    private void show() {
        Dialog<Void> dialog = new Dialog<>();
        dialog.setTitle("Find et projekt · CaDoodle"); dialog.initOwner(root.getScene().getWindow());
        dialog.setResizable(true);
        DialogPane pane = dialog.getDialogPane();
        pane.getStylesheets().addAll(root.getScene().getStylesheets());
        pane.getStyleClass().addAll("kloster-dialog", "kloster-learning-library");
        pane.setPrefSize(790, 650);
        ButtonType cancel = new ButtonType("Arbejd videre", ButtonBar.ButtonData.CANCEL_CLOSE);
        pane.getButtonTypes().add(cancel);
        VBox content = new VBox(12);
        ScrollPane scroll = new ScrollPane(content); scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER); pane.setContent(scroll);
        content.setPadding(new Insets(10));
        showCards(dialog, content);
        dialog.show();
    }

    private void showCards(Dialog<Void> dialog, VBox content) {
        content.getChildren().clear();
        content.getChildren().addAll(text("Hvad vil du skabe?", "kloster-heading"),
            text("Lær værktøjerne trin for trin, og byg et lille produkt. Vælg frit mellem forløbene, eller fold guiden væk undervejs.", null));
        ComboBox<String> path = new ComboBox<>();
        path.getItems().add("Alle projekter");
        for (int i = 1; i <= 4; i++) path.getItems().add(LearningCatalog.stageTitle(i));
        path.getSelectionModel().selectFirst(); path.setMaxWidth(Double.MAX_VALUE);
        path.setAccessibleText("Vælg arbejdsvej");
        TilePane cards = new TilePane(12, 12); cards.setPrefColumns(2);
        Runnable refresh = () -> {
            cards.getChildren().clear(); int selected = path.getSelectionModel().getSelectedIndex();
            for (LearningCatalog.Entry entry : LearningCatalog.entries()) {
                if (selected > 0 && entry.stage() != selected) continue;
                VBox card = new VBox(8, LearningPreview.create(entry.id(), 305, 135),
                    text(entry.title(), "kloster-subheading"), text(entry.summary(), null),
                    text(LearningCourses.steps(entry.id()).size() + " trin · " + LearningCatalog.stageTitle(entry.stage()), "kloster-learning-note"));
                card.setPrefWidth(305); card.setMaxWidth(305);
                Button choose = new Button("Se projekt og vælg"); choose.setMaxWidth(Double.MAX_VALUE);
                choose.setAccessibleText("Se projekt: " + entry.title());
                choose.setOnAction(event -> showProject(dialog, content, entry));
                card.getChildren().add(choose); card.getStyleClass().add("kloster-learning-card");
                cards.getChildren().add(card);
            }
        };
        path.setOnAction(event -> refresh.run()); refresh.run();
        content.getChildren().addAll(path, cards);
    }

    private void showProject(Dialog<Void> dialog, VBox content, LearningCatalog.Entry entry) {
        content.getChildren().clear();
        Button back = new Button("← Alle projekter"); back.setOnAction(event -> showCards(dialog, content));
        content.getChildren().addAll(back, text(entry.title(), "kloster-heading"),
            text(LearningCatalog.stageTitle(entry.stage()), "kloster-learning-note"),
            LearningPreview.create(entry.id(), 305, 160), text("Et eksempel · du vælger den færdige form", "kloster-learning-note"),
            text("Det får du", "kloster-learning-label"), text(entry.gets(), null),
            text("Du skal gøre", "kloster-learning-label"));
        for (int i = 0; i < entry.tasks().size(); i++) content.getChildren().add(text((i + 1) + ". " + entry.tasks().get(i), null));
        VBox overview = new VBox(6);
        var steps = LearningCourses.steps(entry.id());
        for (int i = 0; i < steps.size(); i++) overview.getChildren().add(text((i + 1) + ". " + steps.get(i).title(), null));
        TitledPane route = new TitledPane("Se forløbets " + steps.size() + " trin", overview); route.setExpanded(false);
        content.getChildren().add(route);
        content.getChildren().add(text(entry.practice(), "kloster-learning-note"));
        TextField name = new TextField("MIT DESIGN"); name.setMaxWidth(305);
        name.setAccessibleText("Tekst i din model");
        name.setTextFormatter(new TextFormatter<String>(change -> change.getControlNewText().codePointCount(0,
            change.getControlNewText().length()) <= 18 && !change.getControlNewText().contains("\n") ? change : null));
        if (entry.hasText()) content.getChildren().addAll(text("Tekst i din model (højst 18 tegn)", null), name);
        Label status = text("Du vælger, om din aktuelle tegning skal gemmes, før det nye projekt starter.", "kloster-learning-note");
        Button start = new Button("Start dette projekt"); start.getStyleClass().add("kloster-primary");
        start.setOnAction(event -> start(dialog, content, entry, name.getText(), status));
        content.getChildren().addAll(start, status);
    }

    private void start(Dialog<Void> dialog, VBox content, LearningCatalog.Entry entry, String label, Label status) {
        CaDoodleFile previous = ap.get();
        ProjectNaming.Name previousName = ProjectNaming.name(previous);
        dialog.hide();
        ProjectCloseGuard.requestFinish(root, result -> {
            if (!"completed".equals(result)) { dialog.show(); return; }
            ProjectNaming.Name pupil = previousName != null ? previousName : ProjectNaming.name(previous);
            dialog.setOnHidden(event -> {
                if (!ProjectStartDialog.editingAllowed(root)) ProjectStartDialog.activate(root);
            });
            dialog.show();
            startPrepared(dialog, content, entry, label, status, pupil);
        });
    }

    private void startPrepared(Dialog<Void> dialog, VBox content, LearningCatalog.Entry entry, String label,
            Label status, ProjectNaming.Name pupil) {
        CaDoodleFile previous = ap.get();
        if (previous == null || !previous.isInitialized() || previous.isOperationRunning() || ModelEditQueue.isBusy(ap)) {
            status.setText("Vent, til din seneste ændring er færdig, og prøv igen."); return;
        }
        content.setDisable(true);
        Node cancel = dialog.getDialogPane().lookupButton(dialog.getDialogPane().getButtonTypes().get(0));
        var cancelled = new java.util.concurrent.atomic.AtomicBoolean();
        dialog.setOnCloseRequest(event -> cancelled.set(true));
        button.setDisable(true);
        status.setText("Bygger projektet …");
        Thread worker = new Thread(() -> {
            boolean switched = false;
            CaDoodleFile starterFile = null;
            try {
                String stem = "Projekt-" + entry.id() + "-" + UUID.randomUUID().toString().substring(0, 8);
                File destination = ActiveProject.getWorkingDir().toPath().resolve(stem).resolve(stem + ".doodle").toFile();
                CaDoodleFile starter = LearningProjectService.create(destination, entry.id(), label);
                starterFile = starter;
                starter.close();
                if (cancelled.get()) return;
                var locked = new java.util.concurrent.CountDownLatch(1);
                Platform.runLater(() -> {
                    if (!cancelled.get()) {
                        cancel.setDisable(true);
                        dialog.setOnCloseRequest(event -> event.consume());
                        status.setText("Åbner det færdige projekt …");
                    }
                    locked.countDown();
                });
                if (!locked.await(30, java.util.concurrent.TimeUnit.SECONDS))
                    throw new IllegalStateException("Værkstedet svarer ikke. Starteren ligger under Projekter.");
                if (cancelled.get()) return;
                CaDoodleFile opened;
                synchronized (ap) {
                    if (ap.get() != previous) throw new IllegalStateException("Det aktive projekt blev ændret. Åbn den nye starter under Projekter.");
                    switched = true;
                    opened = ap.setActiveProject(destination);
                    if (!destination.equals(opened.getSelf())) throw new IllegalStateException("Starteren kunne ikke åbnes.");
                    opened.initialize();
                    if (!opened.isInitialized()) throw new IllegalStateException("Projektet blev ikke færdigt med at åbne.");
                    ProjectNaming.cleanupFinishedWorkingCopy(previous, opened);
                }
                Platform.runLater(() -> {
                    if (ap.get() == opened) {
                        if (pupil != null) ProjectNaming.nameDraft(opened,
                            new ProjectNaming.Name(pupil.schoolClass(), pupil.firstName(), opened.getMyProjectName()));
                        if (pupil != null) ProjectStartDialog.openedExternally(root);
                        if (root.lookup("#fileNameBox") instanceof TextField title) title.setText(opened.getMyProjectName());
                        displayed = opened; showGuidance(entry);
                        if (root.lookup("#fitViewButton") instanceof Button fit) fit.fire();
                    }
                    dialog.setOnCloseRequest(null); dialog.close();
                    if (pupil == null && ap.get() == opened) ProjectStartDialog.nameCurrentModel(root);
                });
            } catch (Exception failure) {
                if (switched) try { synchronized (ap) { ap.setActiveProject(previous.getSelf()).initialize(); } }
                catch (Exception restore) { failure.addSuppressed(restore); }
                failure.printStackTrace();
                Platform.runLater(() -> {
                    status.setText("Projektet kunne ikke åbnes: " + failure.getMessage());
                    content.setDisable(false); cancel.setDisable(false); dialog.setOnCloseRequest(null);
                });
            } finally {
                if (starterFile != null) try { ProjectNaming.cleanupFinishedWorkingCopy(starterFile, ap.get()); }
                catch (java.io.IOException cleanupFailure) { cleanupFailure.printStackTrace(); }
                Platform.runLater(() -> button.setDisable(false));
            }
        }, "Learning-project-create");
        worker.setDaemon(true); worker.start();
    }

    private static void save(CaDoodleFile file) throws Exception {
        long until = System.currentTimeMillis() + 10000;
        while (true) try { file.save(true); return; }
        catch (SaveOverwriteException busy) {
            if (System.currentTimeMillis() >= until) throw busy;
            Thread.sleep(100);
        }
    }
}
