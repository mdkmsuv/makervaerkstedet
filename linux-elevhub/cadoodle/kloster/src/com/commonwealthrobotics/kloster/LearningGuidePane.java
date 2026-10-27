package com.commonwealthrobotics.kloster;

import java.nio.file.Path;
import java.util.List;
import java.util.function.Consumer;
import javafx.geometry.Insets;
import javafx.scene.control.*;
import javafx.scene.layout.*;

/** One actionable step at a time; navigation never performs modelling operations. */
final class LearningGuidePane extends VBox {
    private final LearningCatalog.Entry entry;
    private final List<LearningCourses.Step> steps;
    private final Path project;
    private final Consumer<List<String>> highlight;
    private final Runnable collapse;
    private final ComboBox<String> jump = new ComboBox<>();
    private final VBox body = new VBox(9);
    private final ScrollPane scroll = new ScrollPane(body);
    private final Button previous = new Button("← Tilbage");
    private final Button next = new Button("Næste trin →");
    private final Label status = label("", "kloster-learning-note");
    private int index;
    private boolean completed;
    private boolean updatingPicker;

    LearningGuidePane(LearningCatalog.Entry entry, Path project, Consumer<List<String>> highlight, Runnable collapse) {
        this.entry = entry; this.project = project; this.highlight = highlight; this.collapse = collapse;
        steps = LearningCourses.steps(entry.id());
        LearningProgress.State stored = LearningProgress.read(project, entry.id(), steps.size());
        index = stored.step(); completed = stored.completed();
        setSpacing(7); setPadding(new Insets(8)); setMinHeight(0);
        jump.setId("learning-step-picker"); jump.setMaxWidth(Double.MAX_VALUE);
        jump.setAccessibleText("Hop til et trin i guiden");
        for (int i = 0; i < steps.size(); i++) jump.getItems().add((i + 1) + ". " + steps.get(i).title());
        jump.getSelectionModel().selectedIndexProperty().addListener((source, before, after) -> {
            int selected = after.intValue();
            if (!updatingPicker && selected >= 0 && (selected != index || completed)) { index = selected; completed = false; changed(); }
        });
        scroll.setId("learning-step-scroll"); scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        scroll.setPrefViewportHeight(200); scroll.setMinHeight(60); VBox.setVgrow(scroll, Priority.ALWAYS);
        previous.setId("learning-step-back"); next.setId("learning-step-next");
        next.getStyleClass().add("kloster-primary");
        previous.setOnAction(event -> { if (completed) completed = false; else index--; changed(); });
        next.setOnAction(event -> { if (index + 1 < steps.size()) index++; else completed = true; changed(); });
        previous.setMaxWidth(Double.MAX_VALUE); next.setMaxWidth(Double.MAX_VALUE);
        HBox navigation = new HBox(5, previous, next); HBox.setHgrow(next, Priority.ALWAYS);
        Button free = new Button("Arbejd frit · fold guiden sammen"); free.setId("learning-free-work");
        free.setWrapText(true); free.setMaxWidth(Double.MAX_VALUE); free.setOnAction(event -> collapse.run());
        getChildren().addAll(jump, scroll, navigation, status, free);
        render();
    }

    private void changed() {
        render(); scroll.setVvalue(0);
        try { LearningProgress.write(project, entry.id(), new LearningProgress.State(index, completed)); }
        catch (java.io.IOException failure) { status.setText("Trinnet kunne ikke huskes. Modellen er stadig åben."); }
    }

    private void render() {
        updatingPicker = true;
        try { jump.getSelectionModel().select(index); } finally { updatingPicker = false; }
        previous.setDisable(index == 0 && !completed);
        next.setVisible(!completed); next.setManaged(!completed);
        next.setText(index == steps.size() - 1 ? "Afslut guide ✓" : "Næste trin →");
        body.getChildren().clear();
        status.setText("Du tjekker selv resultatet og vælger næste trin.");
        if (completed) {
            body.getChildren().addAll(label("Guiden er gennemgået", "kloster-learning-label"),
                LearningPreview.create(entry.id(), 225, 120),
                label("Dit produkt er dit eget. Drej modellen, og fortæl en makker, hvad du har ændret og hvorfor.", null),
                label(entry.check(), null), label("Prøv også", "kloster-learning-label"), label(entry.challenge(), null));
            Button revisit = new Button("Gennemgå trinnene igen"); revisit.setWrapText(true);
            revisit.setOnAction(event -> { index = 0; completed = false; changed(); }); body.getChildren().add(revisit);
            return;
        }
        LearningCourses.Step step = steps.get(index);
        Label counter = label("TRIN " + (index + 1) + " AF " + steps.size(), "kloster-learning-counter");
        counter.setId("learning-step-counter");
        body.getChildren().addAll(counter, label(step.title(), "kloster-subheading"),
            label(step.instruction(), "kloster-learning-instruction"),
            label("Tjek før du går videre", "kloster-learning-label"), label(step.check(), null));
        VBox hints = new VBox(8, LearningStepIllustration.create(step.illustration()), label(step.hint(), null));
        if (!step.tools().isEmpty()) {
            Button tool = new Button("Vis værktøjet i vinduet"); tool.setWrapText(true);
            tool.setOnAction(event -> highlight.accept(step.tools())); hints.getChildren().add(tool);
        }
        TitledPane help = new TitledPane("Vis mig hvordan", hints); help.setExpanded(false);
        TitledPane example = new TitledPane("Se et muligt slutprodukt", LearningPreview.create(entry.id(), 225, 120));
        example.setExpanded(false);
        body.getChildren().addAll(help, example);
    }

    private static Label label(String value, String style) {
        Label label = new Label(value); label.setWrapText(true); label.setMaxWidth(Double.MAX_VALUE); label.setMinWidth(0);
        if (style != null) label.getStyleClass().add(style);
        return label;
    }
}
