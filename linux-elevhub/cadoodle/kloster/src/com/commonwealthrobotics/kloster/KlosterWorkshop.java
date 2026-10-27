package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine;
import com.neuronrobotics.sdk.addons.kinematics.math.RotationNR;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import javafx.animation.KeyFrame;
import javafx.animation.Timeline;
import javafx.beans.binding.Bindings;
import javafx.scene.Group;
import javafx.scene.control.Button;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.Tooltip;
import javafx.scene.layout.*;
import javafx.scene.text.Font;
import javafx.util.Duration;

/** Small pinned extension. No reference geometry enters the CaDoodle project. */
public final class KlosterWorkshop {
    private final SketchGeometry.Drawing sketch = new SketchGeometry.Drawing();
    private final SketchGeometry.Drawing scribble = new SketchGeometry.Drawing();
    private final ActiveProject ap;
    private final SelectionSession session;
    private final BowlerStudio3dEngine engine;
    private final Pane root;
    private boolean plateAdded;

    public static void install(ActiveProject ap, SelectionSession session,
            BowlerStudio3dEngine engine, Pane root, GridPane topBar) {
        new KlosterWorkshop(ap, session, engine, root, topBar);
    }

    private KlosterWorkshop(ActiveProject ap, SelectionSession session,
            BowlerStudio3dEngine engine, Pane root, GridPane topBar) {
        this.ap=ap; this.session=session; this.engine=engine; this.root=root;
        ProjectCloseGuard.install(root, ap);
        ProjectStartDialog.install(root, ap);
        ProjectCloseGuard.afterReset(root, () -> {
            sketch.marks.clear(); scribble.marks.clear();
            session.clearSelection(); session.clearScreen();
            if (root.lookup("#fileNameBox") instanceof javafx.scene.control.TextField title)
                title.setText(ap.get().getMyProjectName());
        });
        new ProjectFinishInbox(ap, root);
        Font.loadFont(KlosterWorkshop.class.getResourceAsStream("/com/commonwealthrobotics/metrophobic-latin.ttf"),14);
        Button draw=new Button("2D-skitse → 3D"), free=new Button("Frihånd → 3D"), plate=new Button("Vis hele pladen");
        draw.getStyleClass().add("kloster-primary");
        draw.setOnAction(e->new SketchDialog(root.getScene().getWindow(),ap,session,false,sketch).show());
        free.setOnAction(e->new SketchDialog(root.getScene().getWindow(),ap,session,true,scribble).show());
        plate.setOnAction(e->engine.focusOrientation(new TransformNR(0,0,0,new RotationNR(0,0,-45)),new TransformNR(0,0,0),-330));
        Group rulerGroup = engine.getRulerGroup();
        rulerGroup.setVisible(false);
        ToggleButton rulers = new ToggleButton("Vis linealer");
        rulers.setId("kloster-rulers-toggle");
        rulers.selectedProperty().bindBidirectional(rulerGroup.visibleProperty());
        rulers.textProperty().bind(Bindings.when(rulers.selectedProperty())
                .then("Skjul linealer").otherwise("Vis linealer"));
        rulers.setTooltip(new Tooltip("Vis eller skjul X-, Y- og Z-linealerne. Brug linealikonet eller R for at placere dem."));
        Button send = new PrintSubmission(ap, session, root).button();
        LearningLibrary library = new LearningLibrary(ap, root);
        new WorkshopLayout(root, topBar, draw, free, rulers, plate, send, library.button(), library.guidance());
        Button saveProject = new Button("Gem projekt");
        saveProject.setId("kloster-save-project"); saveProject.setMinHeight(44);
        saveProject.getStyleClass().add("kloster-header-button");
        saveProject.setOnAction(event -> ProjectCloseGuard.requestSave(root));
        if (root.lookup("#fileNameBox") != null && root.lookup("#fileNameBox").getParent() instanceof HBox header)
            header.getChildren().add(4, saveProject);
        root.addEventFilter(javafx.scene.input.KeyEvent.KEY_PRESSED, event -> {
            if (event.isShortcutDown() && event.getCode() == javafx.scene.input.KeyCode.S) {
                event.consume(); ProjectCloseGuard.requestSave(root);
            }
        });
        TouchInteractionModes.install(ap, session, engine, root);
        OperationStatus.install(ap, (javafx.scene.control.Label) root.lookup("#kloster-operation-status"));
        new ProjectInbox(ap, session, root);
        if (root.lookup("#newDoodle") instanceof Button fresh)
            fresh.setOnAction(event -> ProjectCloseGuard.requestFinish(root, result -> {
                if ("completed".equals(result)) ProjectStartDialog.activate(root);
            }));
        if (root.lookup("#homeButton") instanceof Button projects) {
            projects.setOnAction(event -> ProjectStartDialog.openProjects(root));
        }
        Timeline ready=new Timeline(new KeyFrame(Duration.millis(500),e->{
            if(!plateAdded&&engine.getWorkplaneGroup()!=null){addPlate();}
        }));ready.setCycleCount(10);ready.play();
        System.out.println("KlosterWorkshop 2026-10-05 update 11 candidate: child-first layout, direct-touch navigation and cancellable rotation loaded");
    }

    private void addPlate(){
        plateAdded=true;
        // Precomputed glyphs avoid native-font differences and startup work.
        engine.addControlNode(WorkplaneReference.create());
    }
}
