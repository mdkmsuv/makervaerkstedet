package com.commonwealthrobotics.kloster;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.ResourceBundle;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.jar.JarFile;
import java.util.regex.Pattern;
import java.util.zip.ZipEntry;

import javafx.application.Platform;
import javafx.embed.swing.SwingFXUtils;
import javafx.fxml.FXMLLoader;
import javafx.geometry.Bounds;
import javafx.scene.Node;
import javafx.scene.Parent;
import javafx.scene.Scene;
import javafx.scene.control.Accordion;
import javafx.scene.control.Button;
import javafx.scene.control.ButtonBase;
import javafx.scene.control.ComboBox;
import javafx.scene.control.Labeled;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.TextField;
import javafx.scene.control.TitledPane;
import javafx.scene.control.ToggleButton;
import javafx.scene.control.Tooltip;
import javafx.scene.image.WritableImage;
import javafx.scene.paint.Color;
import javafx.scene.shape.Rectangle;
import javafx.scene.layout.GridPane;
import javafx.scene.layout.Pane;
import javafx.scene.layout.VBox;
import javafx.scene.text.Font;
import javax.imageio.ImageIO;

/**
 * GUI layout probe for the real upstream MainWindow.fxml and the Kloster overlay.
 *
 * Run this with the assembled CaDoodle jar on the classpath. The FXML is loaded
 * without MainController, after removing only event-handler/controller metadata;
 * all nodes, IDs, skins and CSS remain the packaged ones.
 */
public final class WorkshopLayoutProbe {
    private static final String FXML = "com/commonwealthrobotics/MainWindow.fxml";
    private static final Pattern HANDLER = Pattern.compile("\\s+(?:on[A-Za-z]+|fx:controller)=\"[^\"]*\"");
    private static final List<String> NATIVE_ACTIONS = List.of(
            "copyButton", "pasteButton", "deleteButton", "undoButton", "redoButton",
            "groupButton", "ungroupButton", "alignButton", "mirronButton", "dropToWorkplane",
            "importButton", "export", "homeButton", "newDoodle", "logoButton", "settingsButton",
            "workplaneButton", "rulerButton", "searchButton");

    private WorkshopLayoutProbe() {}

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            throw new IllegalArgumentException("Usage: WorkshopLayoutProbe <CaDoodle jar> [snapshot directory]");
        }
        Path jar = Path.of(args[0]).toAbsolutePath();
        Path snapshots = args.length > 1 && !"--no-window".equals(args[1])
                ? Path.of(args[1]).toAbsolutePath() : null;
        if (!Files.isRegularFile(jar)) throw new IOException("Missing CaDoodle jar: " + jar);
        if (snapshots != null) Files.createDirectories(snapshots);

        final Throwable[] failure = new Throwable[1];
        CountDownLatch done = new CountDownLatch(1);
        Platform.setImplicitExit(false);
        Platform.startup(() -> {
            try {
                Font.loadFont(WorkshopLayoutProbe.class.getResourceAsStream(
                        "/com/commonwealthrobotics/metrophobic-latin.ttf"), 14);
                List<String> failures = new ArrayList<>();
                for (int[] size : new int[][] {{1024, 768}, {1280, 800}, {1366, 768}, {1920, 1080}}) {
                    try {
                        probe(jar, size[0], size[1], snapshots);
                    } catch (Throwable error) {
                        String detail = size[0] + "x" + size[1] + ": " + error.getMessage();
                        failures.add(detail);
                        System.err.println("WorkshopLayoutProbe FAIL " + detail);
                    }
                }
                if (!failures.isEmpty()) throw new AssertionError(String.join("; ", failures));
                System.out.println("WorkshopLayoutProbe PASS: 1024x768, 1280x800, 1366x768 and 1920x1080 (no-window)");
            } catch (Throwable error) {
                failure[0] = error;
            } finally {
                done.countDown();
            }
        });
        if (!done.await(120, TimeUnit.SECONDS)) throw new IllegalStateException("JavaFX probe timed out");
        Platform.exit();
        if (failure[0] != null) {
            failure[0].printStackTrace(System.err);
            System.exit(1);
        }
    }

    private static void probe(Path jar, int width, int height, Path snapshots) throws Exception {
        FXMLLoader loader = new FXMLLoader();
        loader.setLocation(new URL("jar:" + jar.toUri().toURL() + "!/" + FXML));
        loader.setResources(danishBundle(jar));
        byte[] fxml = sanitizedFxml(jar);
        Pane root = loader.load(new ByteArrayInputStream(fxml));
        root.setPrefSize(width, height);

        hideControllerOwnedChrome(root);
        Button sketch = new Button("2D-skitse");
        Button freehand = new Button("Frihånd");
        ToggleButton rulers = new ToggleButton("Vis linealer");
        rulers.setId("kloster-rulers-toggle");
        Button fitPlate = new Button("Vis pladen");
        Button sendPrint = new Button("Send til printer");
        Button library = new Button("Find et projekt");
        library.setId("kloster-learning-library");
        TitledPane guide = new TitledPane("Fortsæt med din model · trin for trin",
                new VBox(new Button("Arbejd frit · fold guiden sammen")));
        guide.setId("kloster-project-guide");
        guide.setExpanded(false);
        guide.setVisible(true);
        guide.setManaged(true);
        Map<String, Integer> fired = installNativeActions(root);
        GridPane topBar = node(root, "topBar", GridPane.class);
        new WorkshopLayout(root, topBar, sketch, freehand, rulers, fitPlate, sendPrint, library, guide);
        TouchInteractionModes.populateControls(node(root, "kloster-touch-tools", javafx.scene.layout.HBox.class),
            new TouchInteractionModes.Router(node -> null, node -> true, model -> {}, () -> {},
                (dx, dy, mode) -> {}, () -> {}));
        fired.put("kloster-learning-library", 0);
        library.setOnAction(event -> fired.compute("kloster-learning-library", (key, count) -> count + 1));

        // Add probe tiles after the overlay has reparented the real palette/scroll.
        GridPane palette = node(root, "objectPallet", GridPane.class);
        palette.getChildren().clear();
        String[] shapes = {"Cylinder med afrundet kant", "Kasse med lang tekst", "Kugle",
                "Kegle", "Torus", "Prisme", "Halvkugle", "Rør", "Stjerne", "Hjerte", "Bue", "Tekstfigur"};
        for (int i = 0; i < shapes.length; i++) {
            Button tile = new Button(shapes[i], new Rectangle(34, 34, Color.LIGHTSTEELBLUE));
            tile.setTooltip(new Tooltip(shapes[i]));
            tile.setOnAction(event -> {});
            palette.getChildren().add(tile);
        }

        Scene scene = new Scene(root, width, height);
        // A no-window Scene does not perform the Stage resize pass; size the root
        // explicitly so the probe tests the requested viewport dimensions.
        root.resize(width, height);
        // Populate controller-owned text for legibility only. This fixture does
        // not load a live project or the native shape library.
        node(root,"fileNameBox",TextField.class).setText("Min model");
        setComboLabel(node(root,"shapeCategory",ComboBox.class),"Grundfigurer");
        setComboLabel(node(root,"snapGrid",ComboBox.class),"1 mm");
        Node advanced = node(root,"advancedButtons",Node.class);
        advanced.setVisible(false); // ActiveProject's default is basic mode.
        root.applyCss();
        root.layout();
        if (snapshots != null) saveSnapshot(scene, snapshots.resolve("workshop-default-" + width + "x" + height + ".png"));

        assertVisibleInsideScene(root, width, height);
        GridPane laidOutTopBar = node(root, "topBar", GridPane.class);
        GridPane laidOutControlBar = node(root, "controlBar", GridPane.class);
        require(laidOutTopBar.getChildren().size() == 1, "header layout did not produce one managed row");
        require(laidOutControlBar.getChildren().size() == 1, "toolbar layout did not produce one managed row");
        assertDescendantsInside(laidOutTopBar, "header");
        assertDescendantsInside(laidOutControlBar, "toolbar");
        for (String id : List.of("topBar", "controlBar", "drawerArea", "objectPallet", "view3d")) {
            assertNoLayoutSiblingOverlap(find(root, id));
        }
        assertToolbar((Parent) laidOutControlBar.getChildren().get(0));
        assertCoreFontSizes(root);
        assertNoTruncatedLabels(root);
        assertTwoPaletteColumns(palette);
        assertNativeStateAndActions(root, fired);
        library.fire();
        require(fired.get("kloster-learning-library") == 1, "project library action was lost during relocation");
        assertSearchRemainsManaged(root);
        assertHelpersDisclosure(root, rulers);
        if (snapshots != null) saveSnapshot(scene, snapshots.resolve("workshop-layout-" + width + "x" + height + ".png"));

        // Expose the native advanced row as MainController.setAdvancedMode does.
        // Labels must remain discoverable without a hover, even at 1024px.
        advanced.setVisible(true);
        root.applyCss(); root.layout();
        assertDescendantsInside(laidOutControlBar,"advanced toolbar");
        assertNoLayoutSiblingOverlap(laidOutControlBar);
        assertToolbar((Parent) laidOutControlBar.getChildren().getFirst());
        assertNoTruncatedLabels(root);
        if (snapshots != null) saveSnapshot(scene,snapshots.resolve("workshop-advanced-"+width+"x"+height+".png"));
        advanced.setVisible(false);
        root.applyCss(); root.layout();
        require(!advanced.isManaged(),"hidden advanced tools still occupy the basic workspace");
    }

    @SuppressWarnings({"rawtypes","unchecked"})
    private static void setComboLabel(ComboBox box,String label) {
        box.getItems().setAll(label);
        box.setValue(label);
    }

    private static Map<String, Integer> installNativeActions(Pane root) {
        Map<String, Integer> fired = new HashMap<>();
        for (String id : NATIVE_ACTIONS) {
            ButtonBase button = node(root, id, ButtonBase.class);
            fired.put(id, 0);
            button.setOnAction(event -> fired.compute(id, (key, count) -> count + 1));
        }
        node(root, "copyButton", ButtonBase.class).setDisable(true);
        return fired;
    }

    private static void hideControllerOwnedChrome(Pane root) {
        for (String id : List.of("RobotLabHolder", "componentTreeHolder", "timelineHolder",
                "RobotLabDrawer", "componentTreeDrawer", "timelineButton", "drawerButton")) {
            Node chrome = find(root, id);
            if (chrome != null) {
                chrome.setVisible(false);
                chrome.setManaged(false);
            }
        }
    }

    private static void assertNativeStateAndActions(Pane root, Map<String, Integer> fired) {
        ButtonBase copy = node(root, "copyButton", ButtonBase.class);
        require(copy.isDisable(), "copyButton disabled state was lost during relocation");
        for (String id : NATIVE_ACTIONS) {
            ButtonBase control = node(root, id, ButtonBase.class);
            if (!control.isDisable()) control.fire();
            require(fired.get(id) == (control.isDisable() ? 0 : 1),
                    id + " action or disabled state was lost during relocation");
        }
    }

    private static void assertSearchRemainsManaged(Pane root) {
        TextField search = node(root, "searchField", TextField.class);
        search.setDisable(true);
        require(!search.isVisible() && !search.isManaged(), "disabled search field should be hidden from layout");
        search.setDisable(false);
        require(search.isVisible() && search.isManaged(), "enabled search field did not rejoin layout");
    }

    private static void assertHelpersDisclosure(Pane root, ToggleButton rulers) {
        Button placeRuler = node(root, "rulerButton", Button.class);
        rulers.setSelected(false);
        require(!placeRuler.isVisible() && !placeRuler.isManaged(), "ruler placement control should start hidden");
        rulers.setSelected(true);
        require(placeRuler.isVisible() && placeRuler.isManaged(), "ruler placement control did not follow toggle");
        TitledPane helpers = titledPaneContaining(root, "workplaneButton");
        require(helpers != null && find(helpers, "kloster-rulers-toggle") != null,
                "workplane and rulers are not inside a collapsible helper pane");
        helpers.setExpanded(true);
        root.applyCss(); root.layout();
        ScrollPane sidebar = node(root, "kloster-sidebar-scroll", ScrollPane.class);
        sidebar.setVvalue(1);
        root.applyCss(); root.layout();
        Node sidebarViewport = sidebar.lookup(".viewport");
        Bounds visible = sidebarViewport.localToScene(sidebarViewport.getBoundsInLocal());
        for (String id : List.of("workplaneButton", "kloster-rulers-toggle")) {
            Node helper = find(helpers, id);
            require(helper != null && helper.isVisible() && helper.isManaged(), id + " is unreachable when helpers are open");
            Bounds bounds = helper.localToScene(helper.getBoundsInLocal());
            require(bounds.getWidth() >= 44 && bounds.getHeight() >= 44,
                    id + " helper target is too small: " + bounds);
            require(bounds.getMinX() >= visible.getMinX() - 1 && bounds.getMaxX() <= visible.getMaxX() + 1
                    && bounds.getMinY() >= visible.getMinY() - 1 && bounds.getMaxY() <= visible.getMaxY() + 1,
                    id + " cannot be reached by scrolling the sidebar: " + bounds + " / " + visible);
        }
    }

    private static void assertTwoPaletteColumns(GridPane palette) {
        Set<Integer> columns = new HashSet<>();
        for (Node child : palette.getChildren()) {
            Integer column = GridPane.getColumnIndex(child);
            if (column != null) columns.add(column);
            if (child instanceof Button shape) {
                require(shape.getBoundsInParent().getWidth() >= 118 && shape.getBoundsInParent().getHeight() >= 112,
                        "shape target is smaller than 118x112: " + shape.getText());
                require(shape.isWrapText(), "shape label must wrap: " + shape.getText());
            }
        }
        require(columns.size() == 2 && columns.containsAll(Set.of(0, 1)),
                "shape palette must render two columns, found " + columns);
    }

    private static void assertVisibleInsideScene(Pane root, double width, double height) {
        for (String id : List.of("drawerArea", "view3d", "topBar", "controlBar")) {
            Node node = node(root, id, Node.class);
            Bounds bounds = node.localToScene(node.getBoundsInLocal());
            require(bounds.getWidth() > 0 && bounds.getHeight() > 0, id + " is not visible after layout");
            require(bounds.getMinX() >= -1 && bounds.getMinY() >= -1
                    && bounds.getMaxX() <= width + 1 && bounds.getMaxY() <= height + 1,
                    id + " escapes the scene: " + bounds);
        }
    }

    private static void assertDescendantsInside(Node container, String label) {
        Bounds outer = container.localToScene(container.getBoundsInLocal());
        for (Node child : descendants(container)) {
            if (child == container || !isShowing(child) || !child.isManaged()) continue;
            Bounds bounds = child.localToScene(child.getBoundsInLocal());
            require(bounds.getMinX() >= outer.getMinX() - 1 && bounds.getMaxX() <= outer.getMaxX() + 1
                            && bounds.getMinY() >= outer.getMinY() - 1 && bounds.getMaxY() <= outer.getMaxY() + 1,
                    label + " child escapes its layout bounds: " + child + " / " + bounds + " / " + outer);
        }
    }

    private static void assertNoLayoutSiblingOverlap(Node root) {
        if (root == null) return;
        if (root instanceof Parent parent && !(root instanceof javafx.scene.control.Control)) {
            assertNoSiblingOverlap(parent, root.getId() == null ? root.getClass().getSimpleName() : root.getId());
            for (Node child : parent.getChildrenUnmodifiable()) assertNoLayoutSiblingOverlap(child);
        }
    }

    private static void assertNoSiblingOverlap(Parent container, String label) {
        List<Node> visible = new ArrayList<>();
        for (Node child : container.getChildrenUnmodifiable()) if (child.isManaged() && child.isVisible()) visible.add(child);
        for (int i = 0; i < visible.size(); i++) {
            Bounds a = visible.get(i).getBoundsInParent();
            for (int j = i + 1; j < visible.size(); j++) {
                Bounds b = visible.get(j).getBoundsInParent();
                require(!hasPositiveAreaOverlap(a, b), label + " children overlap: " + visible.get(i) + " / " + visible.get(j));
            }
        }
    }

    /** Bounds.intersects also reports a shared edge; only area means overlap. */
    private static boolean hasPositiveAreaOverlap(Bounds a, Bounds b) {
        double width = Math.min(a.getMaxX(), b.getMaxX()) - Math.max(a.getMinX(), b.getMinX());
        double height = Math.min(a.getMaxY(), b.getMaxY()) - Math.max(a.getMinY(), b.getMinY());
        return width > 0.5 && height > 0.5;
    }

    private static void assertToolbar(Parent toolbar) {
        for (Node node : descendants(toolbar)) {
            if (node instanceof ButtonBase control && isShowing(control) && control.isManaged()) {
                Bounds bounds = control.localToScene(control.getBoundsInLocal());
                require(bounds.getWidth() >= 44 && bounds.getHeight() >= 44,
                        "toolbar target is smaller than 44x44: " + control);
            }
        }
    }

    private static void assertCoreFontSizes(Pane root) {
        for (Node node : descendants(root)) {
            if (node instanceof Labeled labeled && isShowing(labeled) && labeled.isManaged()
                    && (labeled.getStyleClass().contains("kloster-tool")
                    || labeled.getStyleClass().contains("kloster-shape-tile")
                    || labeled.getStyleClass().contains("kloster-primary"))) {
                require(labeled.getFont().getSize() >= 13, "core label font is too small: " + labeled.getText());
            }
        }
    }

    private static void assertNoTruncatedLabels(Pane root) {
        for (Node node : descendants(root)) {
            if (!(node instanceof Labeled labeled) || !isShowing(labeled) || !labeled.isManaged()) continue;
            String text = labeled.getText();
            if (text == null || text.isBlank()) continue;
            require(!labeled.isTextTruncated(), "label is truncated: " + text);
        }
    }

    private static boolean isShowing(Node node) {
        for (Node current=node; current!=null; current=current.getParent())
            if (!current.isVisible()) return false;
        return true;
    }

    private static TitledPane titledPaneContaining(Node root, String id) {
        if (root instanceof TitledPane pane && find(pane, id) != null) return pane;
        if (root instanceof Parent parent) for (Node child : parent.getChildrenUnmodifiable()) {
            TitledPane found = titledPaneContaining(child, id); if (found != null) return found;
        }
        return null;
    }

    private static List<Node> descendants(Node root) {
        List<Node> result = new ArrayList<>();
        result.add(root);
        if (root instanceof Parent parent) {
            for (Node child : parent.getChildrenUnmodifiable()) result.addAll(descendants(child));
        }
        return result;
    }

    private static byte[] sanitizedFxml(Path jar) throws IOException {
        try (JarFile archive = new JarFile(jar.toFile())) {
            ZipEntry entry = archive.getJarEntry(FXML);
            if (entry == null) throw new IOException("Jar has no " + FXML);
            try (InputStream input = archive.getInputStream(entry)) {
                String source = new String(input.readAllBytes(), StandardCharsets.UTF_8);
                return HANDLER.matcher(source).replaceAll("").getBytes(StandardCharsets.UTF_8);
            }
        }
    }

    private static ResourceBundle danishBundle(Path jar) throws IOException {
        URLClassLoader resources = new URLClassLoader(new URL[] {jar.toUri().toURL()}, WorkshopLayoutProbe.class.getClassLoader());
        return ResourceBundle.getBundle("lang.Messages", Locale.forLanguageTag("da"), resources);
    }

    @SuppressWarnings("unchecked")
    private static <T extends Node> T node(Node root, String id, Class<T> type) {
        Node found = find(root, id);
        require(found != null && type.isInstance(found), "Missing FXML node " + id);
        return (T) found;
    }

    private static Node find(Node current, String id) {
        if (id.equals(current.getId())) return current;
        if (current instanceof ScrollPane scroll && scroll.getContent() != null) {
            Node found = find(scroll.getContent(), id); if (found != null) return found;
        }
        if (current instanceof TitledPane pane && pane.getContent() != null) {
            Node found = find(pane.getContent(), id); if (found != null) return found;
        }
        if (current instanceof Accordion accordion) {
            for (TitledPane pane : accordion.getPanes()) { Node found = find(pane, id); if (found != null) return found; }
        }
        if (current instanceof Parent parent) {
            for (Node child : parent.getChildrenUnmodifiable()) { Node found = find(child, id); if (found != null) return found; }
        }
        return null;
    }

    private static void saveSnapshot(Scene scene, Path target) throws IOException {
        WritableImage image = scene.snapshot(null);
        ImageIO.write(SwingFXUtils.fromFXImage(image, null), "png", target.toFile());
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
