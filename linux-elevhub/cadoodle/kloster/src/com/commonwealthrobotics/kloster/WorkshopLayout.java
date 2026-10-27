package com.commonwealthrobotics.kloster;

import java.util.ArrayList;
import javafx.application.Platform;
import javafx.collections.ListChangeListener;
import javafx.geometry.Insets;
import javafx.geometry.Orientation;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.Parent;
import javafx.scene.control.*;
import javafx.scene.layout.*;

/** Reorganizes the existing controls; their native actions and disabled states stay intact. */
final class WorkshopLayout {
    private final Pane root;

    WorkshopLayout(Pane root, GridPane topBar, Button sketch, Button scribble,
            ToggleButton rulers, Button fitPlate, Button sendPrint) {
        this(root, topBar, sketch, scribble, rulers, fitPlate, sendPrint, null, null);
    }

    WorkshopLayout(Pane root, GridPane topBar, Button sketch, Button scribble,
            ToggleButton rulers, Button fitPlate, Button sendPrint, Button library, Node guidance) {
        this.root = root;
        root.getStyleClass().add("kloster-workshop");
        node("drawerHolder", HBox.class).setMinHeight(0);
        // Obtain every native node before moving any of its parents.
        GridPane controlBar = node("controlBar", GridPane.class);
        Button importFile = button("importButton"), exportFile = button("export");
        header(topBar, library);
        toolbar(controlBar, importFile, exportFile, sendPrint);
        sidebar(sketch, scribble, rulers, guidance);
        footer((GridPane) topBar.getParent());
        navigation(fitPlate);
    }

    private <T extends Node> T node(String id, Class<T> type) {
        Node found = find(root, id);
        if (!type.isInstance(found)) throw new IllegalStateException("Missing workshop control: " + id);
        return type.cast(found);
    }

    private Button button(String id) { return node(id, Button.class); }

    // FXMLLoader calls us before skins attach ScrollPane/Accordion content to the scene.
    private static Node find(Node current, String id) {
        if (id.equals(current.getId())) return current;
        for (Node child : children(current)) {
            Node found = find(child, id);
            if (found != null) return found;
        }
        return null;
    }

    private static java.util.List<Node> children(Node node) {
        if (node instanceof ScrollPane scroll && scroll.getContent() != null)
            return java.util.List.of(scroll.getContent());
        if (node instanceof TitledPane pane && pane.getContent() != null)
            return java.util.List.of(pane.getContent());
        if (node instanceof Accordion accordion) return new ArrayList<>(accordion.getPanes());
        if (node instanceof Parent parent) return parent.getChildrenUnmodifiable();
        return java.util.List.of();
    }

    private static ScrollPane findScroll(Node current, String contentId) {
        if (current instanceof ScrollPane scroll && find(scroll, contentId) != null) return scroll;
        for (Node child : children(current)) {
            ScrollPane found = findScroll(child, contentId);
            if (found != null) return found;
        }
        return null;
    }

    private static <T extends Node> T detach(T child) {
        if (child.getParent() instanceof Pane parent) parent.getChildren().remove(child);
        return child;
    }

    private static Region space() {
        Region space = new Region();
        HBox.setHgrow(space, Priority.ALWAYS);
        return space;
    }

    private static void plain(ButtonBase button, String title, String style) {
        if (!button.textProperty().isBound()) button.setText(title);
        button.setContentDisplay(ContentDisplay.TEXT_ONLY);
        button.setStyle("");
        button.getStyleClass().add(style);
        button.setFocusTraversable(true);
        button.setMinWidth(Region.USE_PREF_SIZE);
        button.setMaxWidth(Double.MAX_VALUE);
        button.setMinHeight(44);
        button.setPrefHeight(44);
    }

    private static void graphic(ButtonBase button, String name, String description) {
        button.setGraphic(WorkshopIcons.create(name));
        button.setTooltip(new Tooltip(description));
        button.setAccessibleText(description);
    }

    private void header(GridPane topBar, Button library) {
        Button projects = detach(button("homeButton"));
        Button newProject = detach(button("newDoodle"));
        TextField title = detach(node("fileNameBox", TextField.class));
        Button about = detach(button("logoButton"));
        plain(projects, "Projekter", "kloster-header-button");
        plain(newProject, "+ Nyt", "kloster-header-button");
        plain(about, "CaDoodle", "kloster-about");
        graphic(about, "cube", "Om CaDoodle");
        about.setContentDisplay(ContentDisplay.LEFT);
        about.setGraphicTextGap(8);
        title.setStyle("");
        title.getStyleClass().add("kloster-project-title");
        title.setMinWidth(120);
        title.setPrefWidth(220);
        title.setMaxWidth(320);
        title.setMinHeight(44);
        title.setAccessibleText("Projektets navn");
        HBox.setHgrow(title, Priority.ALWAYS);
        HBox.setMargin(title, Insets.EMPTY);
        Label school = new Label("Makerværkstedet");
        school.getStyleClass().add("kloster-brand");
        Label workshop = new Label("3D-værksted");
        workshop.getStyleClass().add("kloster-workshop-title");
        school.managedProperty().bind(school.visibleProperty());
        school.visibleProperty().bind(root.widthProperty().greaterThan(1450));
        workshop.managedProperty().bind(workshop.visibleProperty());
        workshop.visibleProperty().bind(root.widthProperty().greaterThan(1250));
        HBox header = new HBox(10, about, title, projects, newProject, space(), school, workshop);
        if (library != null) {
            plain(library, "Find et projekt", "kloster-header-button");
            header.getChildren().add(4, library);
        }
        Button help = new Button("Hjælp");
        help.setId("kloster-help");
        plain(help, "Hjælp", "kloster-header-button");
        help.setAccessibleText("Hjælp til at bygge og bruge touch");
        help.setOnAction(event -> WorkshopHelp.show(root));
        header.getChildren().add(help);
        header.setAlignment(Pos.CENTER_LEFT);
        header.getStyleClass().add("kloster-header");
        topBar.getChildren().clear();
        topBar.getColumnConstraints().setAll(new ColumnConstraints());
        topBar.getRowConstraints().clear();
        topBar.setMinHeight(56);
        topBar.setPrefHeight(56);
        topBar.add(header, 0, 0);
        GridPane.setHgrow(header, Priority.ALWAYS);
    }

    private Button tool(String id, String title, String icon, String description) {
        Button button = detach(button(id));
        button.setText(title);
        button.setContentDisplay(ContentDisplay.TOP);
        button.setGraphic(WorkshopIcons.createToolbar(icon));
        button.setTooltip(new Tooltip(description));
        button.setAccessibleText(description);
        button.setGraphicTextGap(3);
        button.setAlignment(Pos.CENTER);
        button.setStyle("");
        button.setPrefSize(68, 64);
        button.setMinSize(68, 64);
        button.setMaxSize(68, 64);
        button.setFocusTraversable(true);
        button.getStyleClass().add("kloster-tool");
        return button;
    }

    private void toolbar(GridPane controlBar, Button importFile, Button exportFile, Button sendPrint) {
        // Native advanced controls remain available when advanced mode is selected.
        HBox advanced = detach(node("advancedButtons", HBox.class));
        advanced.managedProperty().bind(advanced.visibleProperty());
        advanced.getStyleClass().add("kloster-advanced-controls");
        sizeAdvancedControls(advanced);
        Button copy = tool("copyButton", "Kopiér", "copy", "Kopiér (Ctrl+C)");
        Button paste = tool("pasteButton", "Indsæt", "paste", "Indsæt (Ctrl+V)");
        Button delete = tool("deleteButton", "Slet", "delete", "Slet (Delete)");
        Button undo = tool("undoButton", "Fortryd", "undo", "Fortryd (Ctrl+Z)");
        Button redo = tool("redoButton", "Gentag", "redo", "Gentag (Ctrl+Y)");
        Button group = tool("groupButton", "Gruppér", "group", "Saml de valgte figurer i en gruppe");
        Button ungroup = tool("ungroupButton", "Opdel", "ungroup", "Opdel gruppen i separate figurer");
        Button align = tool("alignButton", "Justér", "align", "Justér figurer i forhold til hinanden");
        Button mirror = tool("mirronButton", "Spejl", "mirror", "Spejlvend de valgte figurer");
        Button drop = tool("dropToWorkplane", "På plade", "drop", "Sæt figuren på arbejdsplanet");
        MenuButton more = new MenuButton("Flere");
        more.setGraphic(WorkshopIcons.createToolbar("more"));
        more.setTooltip(new Tooltip("Flere værktøjer"));
        more.setAccessibleText("Flere værktøjer");
        more.setContentDisplay(ContentDisplay.TOP);
        more.setGraphicTextGap(3);
        more.setMinSize(68, 64);
        more.setPrefSize(68, 64);
        more.setMaxSize(68, 64);
        more.getStyleClass().addAll("kloster-tool", "kloster-more");
        for (String[] item : new String[][] {{"showAllButton", "Vis skjulte figurer"},
                {"cruiseButton", "Flyt figur til en overflade"},
                {"objectWorkplane", "Arbejdsplan ved figuren"}}) {
            Button original = button(item[0]);
            MenuItem menu = new MenuItem(item[1]);
            menu.disableProperty().bind(original.disableProperty());
            menu.setOnAction(event -> original.fire());
            more.getItems().add(menu);
        }
        plain(importFile, "Importér", "kloster-header-button");
        plain(exportFile, "Eksportér", "kloster-header-button");
        plain(sendPrint, "Send til print", "kloster-primary");
        sendPrint.setAccessibleText("Send til ledig printer");
        sendPrint.setTooltip(new Tooltip("Send hele modellen til en ledig printer"));
        // Wrap whole tool groups on smaller screens instead of squeezing labels
        // or pushing the print button beyond the right edge.
        FlowPane actions = new FlowPane(10, 6);
        actions.setId("kloster-actions");
        actions.setMinWidth(0);
        actions.setPrefWrapLength(1000);
        actions.getChildren().addAll(toolGroup(undo, redo), toolGroup(copy, paste, delete),
                toolGroup(group, ungroup, align, mirror, drop, more),
                toolGroup(detach(importFile), detach(exportFile), sendPrint));
        actions.setAlignment(Pos.CENTER_LEFT);
        actions.getStyleClass().add("kloster-actions");
        HBox touchTools = new HBox(6);
        touchTools.setId("kloster-touch-tools");
        touchTools.setAlignment(Pos.CENTER_LEFT);
        touchTools.getStyleClass().add("kloster-touch-tools");
        touchTools.setVisible(false);
        touchTools.managedProperty().bind(touchTools.visibleProperty());
        VBox rows = new VBox(actions, touchTools, advanced);
        rows.setMinWidth(0);
        controlBar.getChildren().clear();
        controlBar.getColumnConstraints().clear();
        controlBar.getRowConstraints().clear();
        controlBar.setStyle("");
        controlBar.add(rows, 0, 0);
        GridPane.setHgrow(rows, Priority.ALWAYS);
    }

    private static HBox toolGroup(Node... controls) {
        HBox group = new HBox(3, controls);
        group.setAlignment(Pos.CENTER_LEFT);
        group.getStyleClass().add("kloster-tool-group");
        return group;
    }

    private static void sizeAdvancedControls(Node node) {
        if (node instanceof ButtonBase control) {
            control.setMinSize(44, 44);
            control.setPrefHeight(44);
            control.setMaxHeight(Region.USE_PREF_SIZE);
            String title = advancedTitle(control.getId());
            if (title != null) {
                if (!control.textProperty().isBound()) control.setText(title);
                control.setContentDisplay(ContentDisplay.LEFT);
                control.setGraphicTextGap(6);
            }
        } else {
            for (Node child : children(node)) sizeAdvancedControls(child);
        }
    }

    private static String advancedTitle(String id) {
        if (id == null) return null;
        return switch (id) {
            case "hexDistributeButton" -> "Mønster";
            case "boltHoleButton" -> "I cirkel";
            case "hullButton" -> "Omslut";
            case "bendButton" -> "Bøj";
            case "extrudeButton" -> "Forlæng";
            case "filletButton" -> "Afrund";
            case "advancedGroupMenu" -> "Kombinér";
            default -> null;
        };
    }

    private static Separator divider() {
        Separator divider = new Separator(Orientation.VERTICAL);
        divider.setPrefHeight(30);
        divider.setMaxHeight(30);
        HBox.setMargin(divider, new Insets(0, 4, 0, 4));
        return divider;
    }

    private void sidebar(Button sketch, Button scribble, ToggleButton rulers, Node guidance) {
        AnchorPane drawer = node("drawerArea", AnchorPane.class);
        GridPane palette = node("objectPallet", GridPane.class);
        ComboBox<?> category = detach(node("shapeCategory", ComboBox.class));
        TextField search = detach(node("searchField", TextField.class));
        Button searchButton = detach(button("searchButton"));
        Button workplane = detach(button("workplaneButton"));
        Button placeRuler = detach(button("rulerButton"));
        ScrollPane scroll = findScroll(drawer, "objectPallet");
        if (scroll == null) throw new IllegalStateException("Missing shape scroll area");
        detach(scroll);
        palette.getColumnConstraints().clear();
        palette.getRowConstraints().clear();
        palette.setHgap(5);
        palette.setVgap(5);
        palette.setPadding(new Insets(0, 0, 6, 0));
        palette.getChildren().addListener((ListChangeListener<Node>) change -> styleShapes(palette));
        styleShapes(palette);
        detach(palette);
        scroll.setContent(palette);
        scroll.setFitToWidth(true);
        scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        scroll.setVbarPolicy(ScrollPane.ScrollBarPolicy.AS_NEEDED);
        scroll.setMinHeight(100);
        scroll.setPrefHeight(200);
        scroll.getStyleClass().add("kloster-shape-scroll");
        VBox.setVgrow(scroll, Priority.ALWAYS);
        category.setMaxWidth(Double.MAX_VALUE);
        category.setPrefWidth(210);
        category.setMinWidth(0);
        category.setMinHeight(44);
        HBox.setHgrow(category, Priority.ALWAYS);
        category.getStyleClass().add("kloster-category");
        search.setMaxWidth(Double.MAX_VALUE);
        search.setMinWidth(0);
        search.setPromptText("Søg efter en figur …");
        search.setMinHeight(44);
        search.setAccessibleText("Søg efter en figur");
        search.getStyleClass().add("kloster-search");
        HBox.setHgrow(search, Priority.ALWAYS);
        plain(searchButton, "Søg", "kloster-secondary");
        graphic(searchButton, "search", "Slå figursøgning til eller fra");
        searchButton.setContentDisplay(ContentDisplay.GRAPHIC_ONLY);
        searchButton.setMinSize(44, 44);
        search.visibleProperty().bind(search.disabledProperty().not());
        search.managedProperty().bind(search.visibleProperty());
        // The native search action enables the field asynchronously. Focus it
        // after it has rejoined layout, instead of while it is still hidden.
        search.disabledProperty().addListener((observable, before, disabled) -> {
            if (!disabled) Platform.runLater(search::requestFocus);
        });
        HBox categoryRow = new HBox(4, category, searchButton);
        categoryRow.setAlignment(Pos.CENTER_LEFT);
        sketch.setText("Skitse");
        scribble.setText("Frihånd");
        sketch.getStyleClass().remove("kloster-primary");
        graphic(sketch, "sketch", "Tegn en 2D-skitse og giv den højde");
        graphic(scribble, "scribble", "Tegn en frihåndsfigur i 3D");
        for (Button draw : new Button[] {sketch, scribble}) {
            draw.setContentDisplay(ContentDisplay.LEFT);
            draw.setGraphicTextGap(8);
            draw.setMaxWidth(Double.MAX_VALUE);
            draw.setMinHeight(44);
            draw.setPrefHeight(44);
            draw.getStyleClass().add("kloster-drawing-tile");
            HBox.setHgrow(draw, Priority.ALWAYS);
        }
        HBox drawings = new HBox(5, sketch, scribble);
        plain(workplane, "Arbejdsplan", "kloster-helper");
        plain(rulers, "Vis linealer", "kloster-helper");
        graphic(workplane, "workplane", "Placér et arbejdsplan på en flade (W)");
        graphic(rulers, "ruler", "Vis eller skjul linealer");
        workplane.setContentDisplay(ContentDisplay.TOP);
        rulers.setContentDisplay(ContentDisplay.TOP);
        workplane.setPrefHeight(60);
        rulers.setPrefHeight(60);
        HBox.setHgrow(workplane, Priority.ALWAYS);
        HBox.setHgrow(rulers, Priority.ALWAYS);
        HBox helpers = new HBox(5, workplane, rulers);
        plain(placeRuler, "Placér lineal (R)", "kloster-secondary");
        placeRuler.visibleProperty().bind(rulers.selectedProperty());
        placeRuler.managedProperty().bind(placeRuler.visibleProperty());
        Label heading = new Label("Figurer");
        heading.getStyleClass().add("kloster-panel-heading");
        Label hint = new Label("Tryk på en figur. Tryk derefter på pladen.");
        hint.getStyleClass().add("kloster-muted");
        hint.setWrapText(true);
        TitledPane measuring = new TitledPane("Mål og arbejdsplan", new VBox(6, helpers, placeRuler));
        measuring.setId("kloster-measuring-tools");
        measuring.setExpanded(false);
        measuring.setAnimated(false);
        measuring.getStyleClass().add("kloster-details");
        VBox panel = new VBox(7, heading, hint, categoryRow, search, scroll, drawings, measuring);
        if (guidance != null) {
            if (guidance instanceof TitledPane guide) guide.setWrapText(true);
            panel.getChildren().add(0, guidance);
        }
        panel.getStyleClass().add("kloster-shapes-panel");
        // When help/search/measuring are all open on a short screen, scroll the
        // entire sidebar rather than clipping the controls below the palette.
        ScrollPane panelScroll = new ScrollPane(panel);
        panelScroll.setId("kloster-sidebar-scroll");
        panelScroll.setFitToWidth(true);
        panelScroll.setFitToHeight(true);
        panelScroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
        panelScroll.setVbarPolicy(ScrollPane.ScrollBarPolicy.AS_NEEDED);
        panelScroll.setMinHeight(0);
        panelScroll.getStyleClass().add("kloster-sidebar-scroll");
        drawer.getChildren().setAll(panelScroll);
        drawer.setStyle("");
        drawer.setMinWidth(310);
        drawer.setMinHeight(0);
        drawer.setPrefWidth(310);
        drawer.setMaxWidth(310);
        AnchorPane.setTopAnchor(panelScroll, 0.0);
        AnchorPane.setRightAnchor(panelScroll, 0.0);
        AnchorPane.setBottomAnchor(panelScroll, 0.0);
        AnchorPane.setLeftAnchor(panelScroll, 0.0);
    }

    private static void styleShapes(GridPane palette) {
        int index = 0;
        for (Node child : new ArrayList<>(palette.getChildren())) {
            GridPane.setColumnIndex(child, index % 2);
            GridPane.setRowIndex(child, index / 2);
            index++;
            if (child instanceof Button shape) {
                if (!shape.getStyleClass().contains("kloster-shape-tile")) {
                    shape.getStyleClass().add("kloster-shape-tile");
                }
                if (shape.getTooltip() != null) {
                    shape.setText(shape.getTooltip().getText());
                    shape.setAccessibleText(shape.getTooltip().getText());
                }
                shape.setContentDisplay(ContentDisplay.TOP);
                shape.setGraphicTextGap(2);
                shape.setWrapText(true);
                shape.setAlignment(Pos.CENTER);
                shape.setMinSize(118, 112);
                shape.setPrefSize(118, 112);
                shape.setMaxSize(118, 112);
                shape.setFocusTraversable(true);
            }
        }
    }

    private void footer(GridPane layout) {
        ComboBox<?> snap = node("snapGrid", ComboBox.class);
        Button settings = button("settingsButton");
        HBox nativeSettings = (HBox) snap.getParent();
        for (Node child : nativeSettings.getChildren()) {
            if (child instanceof Label label) {
                label.setText("Trin");
                label.setTooltip(new Tooltip("Trin for flytning og størrelsesændring"));
                break;
            }
        }
        // Remove the empty overlay wrapper so it cannot intercept the workspace.
        detach(nativeSettings.getParent());
        snap.getStylesheets().clear();
        snap.setPrefWidth(115);
        snap.setMinWidth(115);
        snap.setMaxWidth(115);
        snap.setMinHeight(44);
        plain(settings, "Indstillinger", "kloster-secondary");
        // settingsButton is inside the detached row; retain its original action.
        nativeSettings.setSpacing(8);
        nativeSettings.getStyleClass().add("kloster-grid-settings");
        Label plate = new Label("Arbejdsplan 180 × 180 mm");
        plate.setTooltip(new Tooltip("Fysisk byggevolumen: 180 × 180 × 180 mm\nElevprint: højst 175 × 175 × 175 mm"));
        plate.getStyleClass().add("kloster-plate-status");
        Label navigation = new Label("Drej: højre mus   ·   Zoom: scroll / to fingre");
        navigation.getStyleClass().add("kloster-muted");
        navigation.visibleProperty().bind(root.widthProperty().greaterThan(1500));
        navigation.managedProperty().bind(navigation.visibleProperty());
        Label operation = new Label();
        operation.setId("kloster-operation-status");
        operation.getStyleClass().add("kloster-muted");
        operation.setVisible(false);
        HBox footer = new HBox(12, plate, operation, space(), navigation, space(), detach(nativeSettings));
        footer.setAlignment(Pos.CENTER_LEFT);
        footer.getStyleClass().add("kloster-statusbar");
        layout.add(footer, 0, 3);
        GridPane.setHgrow(footer, Priority.ALWAYS);
        RowConstraints row = new RowConstraints();
        row.setVgrow(Priority.NEVER);
        layout.getRowConstraints().add(row);
        layout.getRowConstraints().get(2).setVgrow(Priority.ALWAYS);
        layout.getRowConstraints().get(2).setMinHeight(0);
    }

    private void navigation(Button fitPlate) {
        String[][] controls = {{"homeViewButton", "home", "Startvisning"},
            {"fitViewButton", "fit", "Vis hele modellen"}, {"zoomInButton", "plus", "Zoom ind"},
            {"zoomOutButton", "minus", "Zoom ud"}};
        for (String[] item : controls) {
            Button button = button(item[0]);
            button.setStyle("");
            graphic(button, item[1], item[2]);
            button.setContentDisplay(ContentDisplay.GRAPHIC_ONLY);
            button.getStyleClass().add("kloster-navigation");
            button.setTranslateX(10);
            button.setMinSize(44, 44);
            button.setPrefSize(44, 44);
            button.setMaxSize(44, 44);
        }
        GridPane navigation = (GridPane) button("zoomOutButton").getParent();
        navigation.setVgap(7);
        AnchorPane cube = node("viewControlCubeHolder", AnchorPane.class);
        cube.setMinSize(104, 104);
        cube.setPrefSize(104, 104);
        cube.setMaxSize(104, 104);
        navigation.getColumnConstraints().get(0).setPrefWidth(110);
        navigation.getRowConstraints().get(0).setMinHeight(110);
        navigation.getRowConstraints().get(0).setPrefHeight(110);
        navigation.getRowConstraints().get(0).setMaxHeight(110);
        graphic(fitPlate, "cube", "Vis hele arbejdspladen");
        fitPlate.setContentDisplay(ContentDisplay.GRAPHIC_ONLY);
        fitPlate.getStyleClass().add("kloster-navigation");
        fitPlate.setTranslateX(10);
        fitPlate.setMinSize(44, 44);
        fitPlate.setPrefSize(44, 44);
        fitPlate.setMaxSize(44, 44);
        navigation.add(fitPlate, 0, 5);
        Button drawer = button("drawerButton");
        HBox holder = node("drawerHolder", HBox.class);
        AnchorPane shapes = node("drawerArea", AnchorPane.class);
        Runnable updateDrawer = () -> graphic(drawer,
                holder.getChildren().contains(shapes) ? "chevron-right" : "chevron-left",
                holder.getChildren().contains(shapes) ? "Skjul figurer" : "Vis figurer");
        holder.getChildren().addListener((ListChangeListener<Node>) change -> updateDrawer.run());
        updateDrawer.run();
        drawer.setContentDisplay(ContentDisplay.GRAPHIC_ONLY);
        drawer.setStyle("");
        drawer.setMinSize(44, 52);
        drawer.setPrefSize(44, 52);
        drawer.setMaxSize(44, 52);
        drawer.getStyleClass().add("kloster-drawer");
        Accordion inspector = node("shapeConfigurationBox", Accordion.class);
        inspector.setStyle("");
        inspector.getStyleClass().add("kloster-inspector");
        configureInspector(inspector);
    }

    private void configureInspector(Accordion inspector) {
        AnchorPane viewport = node("layerHolder", AnchorPane.class);
        AnchorPane holder = node("shapeConfigurationHolder", AnchorPane.class);
        inspector.setMinSize(0, 0);
        inspector.setPrefWidth(336);
        inspector.setMaxWidth(336);
        holder.setMinHeight(0);
        // The inspector is an overlay. Its preferred content size must never
        // push the camera area beyond the screen or clip the final field.
        var height = javafx.beans.binding.Bindings.createDoubleBinding(() -> {
            double titles = inspector.getPanes().size() * 44.0 + 2;
            if (inspector.getExpandedPane() == null) return titles;
            return Math.min(520, Math.max(titles, viewport.getHeight() - 16));
        }, inspector.expandedPaneProperty(), inspector.getPanes(), viewport.heightProperty());
        inspector.prefHeightProperty().bind(height);
        inspector.maxHeightProperty().bind(height);
        holder.prefHeightProperty().bind(height);
        holder.maxHeightProperty().bind(height);
        for (TitledPane pane : inspector.getPanes()) {
            Node content = pane.getContent();
            if (content == null || content instanceof ScrollPane) continue;
            pane.setContent(null);
            ScrollPane scroll = new ScrollPane(content);
            scroll.setId("kloster-inspector-scroll-" + pane.getId());
            scroll.getStyleClass().add("kloster-inspector-scroll");
            scroll.setFitToWidth(true);
            scroll.setPannable(true);
            scroll.setMinHeight(0);
            scroll.setHbarPolicy(ScrollPane.ScrollBarPolicy.NEVER);
            scroll.setVbarPolicy(ScrollPane.ScrollBarPolicy.AS_NEEDED);
            pane.setContent(scroll);
        }
    }
}
