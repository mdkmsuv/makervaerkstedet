package com.commonwealthrobotics.kloster;

import javafx.geometry.Point3D;
import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.MeshView;
import javafx.scene.text.Font;

/** Checks real vector glyph bounds and reading direction without opening a window. */
public final class WorkplaneReferenceTest {
    public static void main(String[] args) {
        require(Font.loadFont(WorkplaneReferenceTest.class.getResourceAsStream(
                "/com/commonwealthrobotics/metrophobic-latin.ttf"), 14) != null, "bundled font missing");
        var reference = WorkplaneReference.create();
        require(reference.isMouseTransparent(), "reference must not intercept selection");
        require(reference.getChildren().size() == 2, "expected two vector labels");
        for (var node : reference.getChildren()) {
            require(node instanceof MeshView, "labels must use vector geometry");
            var mesh = (MeshView) node;
            var bounds = mesh.getBoundsInParent();
            require(bounds.getMinX() > 65 && bounds.getMaxX() < 85, "labels must stay in front margin");
            require(bounds.getMinY() >= -80.001 && bounds.getMaxY() <= 80.001, "labels need side margins");
            require(bounds.getMinZ() > 0.08 && bounds.getMaxZ() < 0.3, "labels must sit just above grid");
            require(node.isMouseTransparent(), "label must not intercept selection");
            Point3D baseline = mesh.getLocalToParentTransform().deltaTransform(1, 0, 0);
            Point3D up = mesh.getLocalToParentTransform().deltaTransform(0, 1, 0);
            require(baseline.getY() > .999 && up.getX() < -.999,
                    "letters must run left to right, with tops pointing toward the back of the plate");
            var material = (PhongMaterial) mesh.getMaterial();
            require(Color.BLACK.equals(material.getDiffuseColor()) && material.getSelfIlluminationMap() != null,
                    "reference colour must be independent of model lights");
        }
        require(reference.getChildren().get(0).getBoundsInParent().getMaxY() < 0, "heading belongs on left");
        require(reference.getChildren().get(1).getBoundsInParent().getMinY() > 0, "units belong on right");
        System.out.println("PASS: vector workplane labels, reading direction, margins, depth and non-picking");
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
