package com.commonwealthrobotics.kloster;

import javafx.scene.Node;
import javafx.scene.canvas.Canvas;
import javafx.scene.canvas.GraphicsContext;
import javafx.scene.paint.Color;
import javafx.scene.text.Font;

/** Small explanatory diagrams, never objects in the pupil's printable model. */
final class LearningStepIllustration {
    private static final Color INK = Color.web("#40566e"), BLUE = Color.web("#2f6f8f"), RED = Color.web("#c62b2f");
    static Node create(String kind) {
        Canvas canvas = new Canvas(225, 88); GraphicsContext g = canvas.getGraphicsContext2D();
        g.setFill(Color.web("#f1f5f7")); g.fillRect(0, 0, 225, 88);
        g.setFont(Font.font("Metrophobic", 11)); g.setLineWidth(1.6);
        String caption;
        switch (kind) {
            case "move" -> {
                outline(g, 23, 26, 38, 30); arrow(g, 74, 41, 138, 41); block(g, 154, 26, 38, 30);
                caption = "Flyt en valgt del";
            }
            case "size" -> {
                block(g, 58, 17, 96, 39); g.setStroke(RED); g.strokeLine(58, 65, 154, 65);
                g.strokeLine(58, 61, 58, 69); g.strokeLine(154, 61, 154, 69);
                for (double x : new double[]{58, 154}) for (double y : new double[]{17, 56}) {
                    g.setFill(Color.WHITE); g.fillRect(x - 3, y - 3, 6, 6); g.strokeRect(x - 3, y - 3, 6, 6);
                }
                caption = "Vælg en del · skriv et mål";
            }
            case "lift" -> {
                outline(g, 45, 45, 62, 17); block(g, 45, 15, 62, 17); arrow(g, 134, 58, 134, 16);
                caption = "Løft · lad delene overlappe lidt";
            }
            case "copy" -> {
                block(g, 25, 24, 41, 32); arrow(g, 79, 40, 132, 40); block(g, 146, 24, 41, 32);
                caption = "Kopiér → Indsæt → flyt kopien";
            }
            case "hole" -> {
                block(g, 21, 21, 62, 40); g.setFill(Color.web("#9ba9b0")); g.fillOval(41, 30, 23, 23);
                arrow(g, 95, 41, 128, 41); block(g, 144, 21, 62, 40);
                g.setFill(Color.web("#f1f5f7")); g.fillOval(164, 30, 23, 23);
                caption = "Hul + solid → Gruppér";
            }
            case "align" -> {
                block(g, 68, 15, 87, 18); block(g, 86, 40, 51, 21);
                g.setStroke(RED); g.setLineDashes(3, 3); g.strokeLine(111, 6, 111, 67); g.setLineDashes();
                caption = "Justér midterpunkter";
            }
            case "group" -> {
                block(g, 78, 17, 33, 35); g.setFill(Color.web("#3d8f83")); g.fillOval(102, 22, 43, 38);
                g.setStroke(RED); g.setLineDashes(4, 3); g.strokeRect(67, 10, 87, 56); g.setLineDashes();
                caption = "Vælg flere dele → Gruppér";
            }
            case "finish" -> {
                block(g, 79, 27, 69, 34); g.setStroke(RED); g.strokeLine(101, 15, 110, 22); g.strokeLine(110, 22, 127, 8);
                caption = "Se fra flere sider · prøv · forbedr";
            }
            default -> {
                block(g, 83, 20, 60, 37); g.setStroke(RED); g.strokeArc(52, 8, 123, 58, 10, 145, javafx.scene.shape.ArcType.OPEN);
                arrow(g, 57, 35, 56, 20); caption = "Drej visningen · undersøg delene";
            }
        }
        g.setFill(INK); g.fillText(caption, 8, 82);
        canvas.setAccessibleText("Tegnet eksempel: " + caption);
        return canvas;
    }
    private static void block(GraphicsContext g, double x, double y, double w, double h) {
        g.setFill(BLUE); g.fillRect(x, y, w, h);
        g.setFill(Color.web("#83b4ca")); g.fillRect(x, y, w, 5);
    }
    private static void outline(GraphicsContext g, double x, double y, double w, double h) {
        g.setStroke(INK); g.setLineDashes(3, 3); g.strokeRect(x, y, w, h); g.setLineDashes();
    }
    private static void arrow(GraphicsContext g, double x, double y, double endX, double endY) {
        g.setStroke(RED); g.strokeLine(x, y, endX, endY);
        double a = Math.atan2(endY - y, endX - x);
        g.strokeLine(endX, endY, endX - 7 * Math.cos(a - .5), endY - 7 * Math.sin(a - .5));
        g.strokeLine(endX, endY, endX - 7 * Math.cos(a + .5), endY - 7 * Math.sin(a + .5));
    }
}
