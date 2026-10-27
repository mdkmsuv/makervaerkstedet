package com.commonwealthrobotics.kloster;

import eu.mihosoft.vrl.v3d.CSG;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import javafx.application.Platform;
import javafx.scene.canvas.Canvas;
import javafx.scene.paint.Color;

/** Small projections of the actual starter solids. Never adds geometry to a project. */
final class LearningPreview {
    private record Face(double[] x, double[] y, double depth, Color color) { }
    private static final Map<String, CompletableFuture<List<Face>>> CACHE = new ConcurrentHashMap<>();
    private static final java.util.concurrent.Executor WORKER = Executors.newSingleThreadExecutor(r -> {
        Thread thread = new Thread(r, "Learning-preview"); thread.setDaemon(true); return thread;
    });

    static Canvas create(String id, double width, double height) {
        Canvas canvas = new Canvas(width, height);
        canvas.setAccessibleText("Eksempel på " + LearningCatalog.find(id).title());
        var graphics = canvas.getGraphicsContext2D();
        graphics.setFill(Color.web("#f4f3f0")); graphics.fillRect(0, 0, width, height);
        CACHE.computeIfAbsent(id, key -> CompletableFuture.supplyAsync(() -> project(key), WORKER))
            .whenComplete((faces, error) -> Platform.runLater(() -> {
                if (error != null || faces == null || faces.isEmpty()) {
                    graphics.setFill(Color.web("#40505b"));
                    graphics.fillText("Eksempel kunne ikke vises", 12, height / 2);
                } else paint(canvas, faces);
            }));
        return canvas;
    }

    private static List<Face> project(String id) {
        List<Face> faces = new ArrayList<>();
        for (CSG solid : LearningModels.preview(id, "MIT DESIGN")) {
            for (var polygon : solid.getPolygons()) {
                int count = polygon.getVertices().size();
                if (count < 3) continue;
                double[] x = new double[count], y = new double[count];
                double depth = 0;
                for (int i = 0; i < count; i++) {
                    var point = polygon.getVertices().get(i).pos;
                    x[i] = (point.x - point.y) * 0.866;
                    y[i] = (point.x + point.y) * 0.5 - point.z;
                    depth += point.x + point.y + point.z;
                }
                var normal = polygon.plane.getNormal();
                double light = 0.65 + 0.28 * Math.max(0, (normal.x * 0.3 - normal.y * 0.4 + normal.z * 0.85));
                Color base = solid.getColor();
                Color color = Color.color(base.getRed() * light, base.getGreen() * light, base.getBlue() * light);
                faces.add(new Face(x, y, depth / count, color));
            }
        }
        faces.sort(Comparator.comparingDouble(Face::depth));
        return List.copyOf(faces);
    }

    private static void paint(Canvas canvas, List<Face> faces) {
        double minX = Double.POSITIVE_INFINITY, minY = minX, maxX = -minX, maxY = -minX;
        for (Face face : faces) for (int i = 0; i < face.x.length; i++) {
            minX = Math.min(minX, face.x[i]); maxX = Math.max(maxX, face.x[i]);
            minY = Math.min(minY, face.y[i]); maxY = Math.max(maxY, face.y[i]);
        }
        double scale = Math.min((canvas.getWidth() - 36) / (maxX - minX), (canvas.getHeight() - 28) / (maxY - minY));
        double offsetX = (canvas.getWidth() - (maxX - minX) * scale) / 2;
        double offsetY = (canvas.getHeight() - (maxY - minY) * scale) / 2;
        var graphics = canvas.getGraphicsContext2D();
        graphics.setFill(Color.web("#f4f3f0")); graphics.fillRect(0, 0, canvas.getWidth(), canvas.getHeight());
        for (Face face : faces) {
            double[] x = new double[face.x.length], y = new double[x.length];
            for (int i = 0; i < x.length; i++) {
                x[i] = offsetX + (face.x[i] - minX) * scale;
                y[i] = offsetY + (face.y[i] - minY) * scale;
            }
            graphics.setFill(face.color); graphics.fillPolygon(x, y, x.length);
        }
    }
}
