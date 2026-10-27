package com.commonwealthrobotics.kloster;

import javafx.scene.Node;
import javafx.scene.Group;
import javafx.scene.layout.StackPane;
import javafx.scene.paint.Color;
import javafx.scene.shape.SVGPath;
import javafx.scene.shape.StrokeLineCap;
import javafx.scene.shape.StrokeLineJoin;

/** Small, font- and image-free outline icons for the workshop controls. */
final class WorkshopIcons {
    private static final double SIZE = 24;
    private static final Color INK = Color.web("#40566e");
    private static final double TOOLBAR_SIZE = 28;
    private static final Color TOOLBAR_INK = Color.web("#344b63");
    private static final Color TOOLBAR_FILL = Color.web("#c9dce9");

    private WorkshopIcons() { }

    /**
     * Creates a fixed-size icon. For example:
     * {@code copyButton.setGraphic(WorkshopIcons.create("copy"));}
     */
    static Node create(String name) {
        StackPane icon = new StackPane();
        icon.setMinSize(SIZE, SIZE);
        icon.setPrefSize(SIZE, SIZE);
        icon.setMaxSize(SIZE, SIZE);
        icon.setMouseTransparent(true);

        SVGPath path = new SVGPath();
        path.setContent(pathFor(name));
        path.setFill(Color.TRANSPARENT);
        path.setStroke(INK);
        path.setStrokeWidth(1.7);
        path.setStrokeLineCap(StrokeLineCap.ROUND);
        path.setStrokeLineJoin(StrokeLineJoin.ROUND);
        path.getStyleClass().add("workshop-icon");
        icon.getChildren().add(path);
        return icon;
    }

    /** Creates the larger, more explicit silhouette used by the top toolbar. */
    static Node createToolbar(String name) {
        StackPane icon = new StackPane();
        icon.setMinSize(TOOLBAR_SIZE, TOOLBAR_SIZE);
        icon.setPrefSize(TOOLBAR_SIZE, TOOLBAR_SIZE);
        icon.setMaxSize(TOOLBAR_SIZE, TOOLBAR_SIZE);
        icon.setMouseTransparent(true);
        Group layersGroup = new Group();
        String[] layers = toolbarPaths(name);
        for (int i = 0; i < layers.length; i++) {
            SVGPath path = new SVGPath();
            path.setContent(layers[i]);
            path.setFill(fillForToolbar(name, i) ? TOOLBAR_FILL : Color.TRANSPARENT);
            path.setStroke(TOOLBAR_INK);
            path.setStrokeWidth(2.0);
            path.setStrokeLineCap(StrokeLineCap.ROUND);
            path.setStrokeLineJoin(StrokeLineJoin.ROUND);
            if ("mirror".equals(name) && i == 0) path.getStrokeDashArray().addAll(3.0, 3.0);
            path.getStyleClass().add("workshop-icon");
            layersGroup.getChildren().add(path);
        }
        icon.getChildren().add(layersGroup);
        return icon;
    }

    private static boolean fillForToolbar(String name, int layer) {
        return switch (name == null ? "" : name) {
            case "copy", "paste", "delete" -> layer == 0;
            case "mirror" -> layer > 0;
            case "drop" -> layer == 3;
            case "group" -> layer == 1 || layer == 2;
            case "ungroup" -> layer == 0 || layer == 1;
            default -> false;
        };
    }

    private static String[] toolbarPaths(String name) {
        if (name == null) return new String[]{"M5 5h18v18H5z M10 10h8v8h-8z"};
        return switch (name) {
            case "copy" -> new String[]{
                    "M9 8h13v15H9z", "M6 19H4V5h13v3"};
            case "paste" -> new String[]{
                    "M9 6h10v3H9z M7 8H5v16h16v-3", "M9 9h13v15H9z"};
            case "delete" -> new String[]{
                    "M6 8h16 M10 5h8l1 3H9z M8 8l1 16h10l1-16z", "M12 12v8 M16 12v8"};
            case "undo" -> new String[]{"M10 7 4 13l6 6", "M4 13h10c5 0 8 3 8 8"};
            case "redo" -> new String[]{"M18 7l6 6-6 6", "M24 13H14c-5 0-8 3-8 8"};
            case "group" -> new String[]{
                    "M4 5h20v19H4z", "M6 9h9v9H6z", "M17 15a4 4 0 1 0 0 8a4 4 0 1 0 0-8z"};
            case "ungroup" -> new String[]{
                    "M4 5h9v9H4z", "M19 17a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "M15 7l3-3 M8 18l-3 3"};
            case "align" -> new String[]{
                    "M5 4v20", "M8 7h15v3H8z", "M8 13h10v3H8z", "M8 19h13v3H8z"};
            case "mirror" -> new String[]{
                    "M14 4v20", "M11 7 5 10v8l6 3z", "M17 7l6 3v8l-6 3z"};
            case "drop" -> new String[]{
                    "M4 25h20", "M10 18l4 4 4-4", "M14 16v6", "M8 5l6-3 6 3v6l-6 3-6-3z M8 5l6 3 6-3 M14 8v6"};
            case "more" -> new String[]{"M6 14h.01 M14 14h.01 M22 14h.01"};
            default -> new String[]{"M5 5h18v18H5z M10 10h8v8h-8z"};
        };
    }

    private static String pathFor(String name) {
        if (name == null) return generic();
        return switch (name) {
            case "projects" -> "M3.5 7.5h6l2 2h9v9h-17z M3.5 7.5v-2h6l2 2";
            case "new" -> "M12 4v16 M4 12h16";
            case "copy" -> "M8 8h10v11H8z M6 16H4V5h11v2";
            case "paste" -> "M8 5h8v3H8z M6 7H4v13h13v-2 M8 8h10v12H8z";
            case "delete" -> "M5 7h14 M9 4h6l1 3H8z M7 7l1 13h8l1-13 M10 10v7 M14 10v7";
            case "undo" -> "M9 7 4 12l5 5 M4 12h9a6 6 0 0 1 6 6";
            case "redo" -> "M15 7l5 5-5 5 M20 12h-9a6 6 0 0 0-6 6";
            case "group" -> "M4 5h7v7H4z M13 12h7v7h-7z M7.5 12v3h5 M12 8h3v4";
            case "ungroup" -> "M4 5h7v7H4z M13 12h7v7h-7z M11 8h2 M8 12v2 M12 12v2";
            case "align" -> "M4 5h16 M7 5v5h10v-5 M5 19h14 M8 14v5h8v-5";
            case "mirror" -> "M12 4v16 M10 6 5 9v6l5 3z M14 6l5 3v6l-5 3z";
            case "drop" -> "M4 18h16 M7 15l5 5 5-5 M12 4v15";
            case "more" -> "M5 12h.01 M12 12h.01 M19 12h.01";
            case "search" -> "M10.5 5.5a5 5 0 1 0 0 10 5 5 0 0 0 0-10z M14.5 14.5 20 20";
            case "workplane" -> "M3 15l9-5 9 5-9 5z M3 15V9l9-5 9 5v6 M12 10v10";
            case "ruler" -> "M4 17 17 4l3 3L7 20z M8 13l3 3 M11 10l3 3 M14 7l3 3";
            case "sketch" -> "M5 19c2-3 1-6 4-8s5 1 6-2c1-2 2-3 4-4 M5 19l3-1";
            case "scribble" -> "M4 16c2-7 5-8 6-3s4 5 5 0 3-4 5-5 M5 19h14";
            case "home" -> "M4 11l8-7 8 7v8h-5v-5H9v5H4z";
            case "fit" -> "M4 9V4h5 M15 4h5v5 M20 15v5h-5 M9 20H4v-5 M7 7l10 10 M17 7 7 17";
            case "plus" -> "M12 5v14 M5 12h14";
            case "minus" -> "M5 12h14";
            case "cube" -> "M12 3l8 4.5v9L12 21l-8-4.5v-9z M4 7.5l8 4.5 8-4.5 M12 12v9";
            case "print" -> "M6 9V4h12v5 M6 17H4v-6h16v6h-2 M7 15h10v6H7z";
            case "import" -> "M12 4v12 M7 11l5 5 5-5 M5 20h14";
            case "export" -> "M12 20V8 M7 13l5-5 5 5 M5 4h14";
            case "chevron-left" -> "M15 5l-7 7 7 7";
            case "chevron-right" -> "M9 5l7 7-7 7";
            default -> generic();
        };
    }

    private static String generic() {
        return "M5 5h14v14H5z M9 9h6v6H9z";
    }
}
