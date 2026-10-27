package com.commonwealthrobotics.kloster;

import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cylinder;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.Sphere;
import javafx.scene.paint.Color;
import javafx.scene.text.Font;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Native, editable starter models for the four CaDoodle learning paths. */
public final class LearningModels {
    public static final double PLATE = 180.0;
    public static final double MAX_DIMENSION = 175.0;
    private static final Font LABEL_FONT = Font.loadFont(LearningModels.class.getResourceAsStream(
            "/com/commonwealthrobotics/metrophobic-latin.ttf"), 10);
    private static final Color BLUE = Color.web("#2f6f8f");
    private static final Color TEAL = Color.web("#3d8f83");
    private static final Color GOLD = Color.web("#d49a2a");
    private static final Color CORAL = Color.web("#c86f5d");
    private static final Color CHARCOAL = Color.web("#40505b");

    private LearningModels() { }

    /** Returns raw, separately editable solids and hole tools for the requested starter. */
    public static List<CSG> create(String id, String label) {
        String key = normalizeId(id);
        String text = safeLabel(label);
        return switch (key) {
            case "name-tag" -> nameTag(text);
            case "robot" -> robot(text);
            case "game-piece" -> gamePiece(text);
            case "pencil-holder" -> pencilHolder();
            case "desk-sign" -> deskSign(text);
            case "open-box" -> openBox();
            default -> throw new IllegalArgumentException("Ukendt læringsmodel: " + id);
        };
    }

    /** Returns a preview assembly while preserving the raw create() API for editing. */
    public static List<CSG> preview(String id, String label) {
        if (normalizeId(id).equals("pencil2holes") || normalizeId(id).equals("pencil-holder")) {
            return pencilTwoHoles();
        }
        List<CSG> raw = create(id, label);
        String key = normalizeId(id);
        List<CSG> assembled = new ArrayList<>();
        if (key.equals("name-tag") || key.equals("pencil-holder") || key.equals("open-box")) {
            CSG body = raw.get(0).difference(raw.get(1)).setName(raw.get(0).getName());
            assembled.add(body);
            assembled.addAll(raw.subList(2, raw.size()));
        } else {
            assembled.addAll(raw);
        }
        return List.copyOf(assembled);
    }

    private static List<CSG> nameTag(String label) {
        CSG plate = cube(60, 25, 3, 10, 10, 0, "Navneskilt plade", BLUE);
        CSG hole = cylinder(3.2, 4, 16, 16, 0, "Navneskilt skærecylinder", CHARCOAL);
        CSG text = text(label, 38, 8, 1.2, 41, 18.5, 2.7, "Navneskilt tekst", GOLD);
        return List.of(plate, hole, text);
    }

    private static List<CSG> robot(String label) {
        CSG plinth = cube(34, 28, 3, 70, 70, 0, "Robot sokkel", CHARCOAL);
        CSG body = cube(24, 18, 19, 75, 75, 2.5, "Robot krop", BLUE);
        CSG head = cube(19, 17, 10, 77.5, 75.5, 20.8, "Robot hoved", TEAL);
        CSG leftArm = cube(5, 18, 12, 72.5, 75, 7, "Robot venstre arm", CORAL);
        CSG rightArm = cube(5, 18, 12, 96.5, 75, 7, "Robot højre arm", CORAL);
        return List.of(plinth, body, head, leftArm, rightArm);
    }

    private static List<CSG> gamePiece(String label) {
        CSG base = cylinder(14, 3, 42, 42, 0, "Spilbrik fod", BLUE);
        CSG stem = cylinder(5, 22, 42, 42, 2.5, "Spilbrik stilk", TEAL);
        CSG head = new Sphere(10, 24, 16).toCSG().toZMin().movex(42).movey(42).movez(24.2)
                .setName("Spilbrik hoved").setColor(GOLD);
        return List.of(base, stem, head);
    }

    private static List<CSG> pencilHolder() {
        CSG outer = cube(60, 35, 35, 60, 72.5, 0, "Blyantholder massiv blok", TEAL);
        CSG cutter = cube(25.5, 29, 35, 63, 75.5, 3, "Blyantholder skæreblok", CHARCOAL);
        return List.of(outer, cutter);
    }

    private static List<CSG> pencilTwoHoles() {
        CSG outer = cube(60, 35, 40, 60, 72.5, 0, "Blyantholder massiv blok", TEAL);
        CSG left = cube(25.5, 29, 40, 63, 75.5, 3, "Blyantholder venstre skæreblok", CHARCOAL);
        CSG right = cube(25.5, 29, 40, 91.5, 75.5, 3, "Blyantholder højre skæreblok", CHARCOAL);
        return List.of(outer.difference(left).difference(right).setName(outer.getName()));
    }

    private static List<CSG> deskSign(String label) {
        CSG plaque = cube(70, 25, 3, 90, 82, 0, "Bordskilt plade", BLUE);
        CSG top = cube(62, 1.8, 1.1, 94, 85, 2.7, "Bordskilt øvre kant", GOLD);
        CSG bottom = cube(62, 1.8, 1.1, 94, 102.2, 2.7, "Bordskilt nedre kant", GOLD);
        CSG left = cube(1.8, 15.2, 1.1, 94, 86.8, 2.7, "Bordskilt venstre kant", GOLD);
        CSG right = cube(1.8, 15.2, 1.1, 154.2, 86.8, 2.7, "Bordskilt højre kant", GOLD);
        CSG text = text(label, 45, 7, 1.0, 125, 94.5, 2.7, "Bordskilt tekst", CORAL);
        return List.of(plaque, top, bottom, left, right, text);
    }

    private static List<CSG> openBox() {
        CSG outer = cube(80, 50, 20, 20, 110, 0, "Åben æske yderskal", BLUE);
        CSG inner = cube(75, 45, 20, 22.5, 112.5, 3, "Åben æske indersolid", CHARCOAL);
        return List.of(outer, inner);
    }

    private static CSG cube(double width, double depth, double height, double x, double y, double z,
            String name, Color color) {
        // Cube is centered on X/Y; callers specify the lower-left footprint corner.
        return new Cube(width, depth, height).toCSG().toZMin().movex(x + width / 2.0)
                .movey(y + depth / 2.0).movez(z)
                .setName(name).setColor(color);
    }

    private static CSG cylinder(double radius, double height, double x, double y, double z,
            String name, Color color) {
        return new Cylinder(radius, height).toCSG().toZMin().movex(x).movey(y).movez(z)
                .setName(name).setColor(color);
    }

    private static CSG text(String value, double width, double height, double depth, double centerX,
            double centerY, double z, String name, Color color) {
        return CSG.textToSize(value, width, height, depth, LABEL_FONT).moveToCenter().toZMin()
                .movex(centerX).movey(centerY).movez(z).setName(name).setColor(color);
    }

    private static String normalizeId(String id) {
        if (id == null) throw new IllegalArgumentException("Model-id mangler.");
        return id.trim().toLowerCase(Locale.ROOT);
    }

    private static String safeLabel(String label) {
        if (label == null || label.isBlank()) return "MIT DESIGN";
        String compact = label.strip().replaceAll("\\s+", " ");
        int end = compact.offsetByCodePoints(0, Math.min(18, compact.codePointCount(0, compact.length())));
        return compact.substring(0, end);
    }
}
