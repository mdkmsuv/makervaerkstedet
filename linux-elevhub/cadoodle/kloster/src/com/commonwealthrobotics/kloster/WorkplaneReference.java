package com.commonwealthrobotics.kloster;

import java.io.DataInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import javafx.scene.Group;
import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.MeshView;
import javafx.scene.shape.TriangleMesh;
import javafx.scene.transform.Rotate;

/**
 * Non-pickable, screen-facing reference marks for the 180 mm workplane.
 * Bundled vector glyphs stay crisp at any zoom and are identical on both platforms.
 */
final class WorkplaneReference {
    private static final Color INK = Color.web("#4b91ad");

    private WorkplaneReference() { }

    static Group create() {
        Group reference = new Group();
        reference.setId("kloster-a1-mini-reference");
        reference.setMouseTransparent(true);

        PhongMaterial textMaterial = material();
        // At the home camera, +X faces the pupil and +Y points to the right.
        // Both words have 10 mm side margins inside the physical plate.
        reference.getChildren().add(label("workplane", "Arbejdsplan", 78, -50, textMaterial));
        reference.getChildren().add(label("millimetres", "Millimeter", 78, 62.5, textMaterial));
        return reference;
    }

    private static PhongMaterial material() {
        PhongMaterial material = new PhongMaterial(Color.BLACK);
        material.setSpecularColor(Color.BLACK);
        javafx.scene.image.WritableImage ink = new javafx.scene.image.WritableImage(1, 1);
        ink.getPixelWriter().setColor(0, 0, INK);
        material.setSelfIlluminationMap(ink);
        return material;
    }

    private static MeshView label(String asset, String text, double x, double y, PhongMaterial material) {
        MeshView mesh = new MeshView(readMesh(asset, text));
        mesh.setId("workplane-label-" + asset);
        mesh.setTranslateX(x);
        mesh.setTranslateY(y);
        mesh.setTranslateZ(0.12);
        // CSG.textToSize already converts JavaFX's Y-down glyphs to its
        // Y-up mesh convention. Only the quarter-turn used by the plate
        // layout belongs here; a second X flip mirrors the lettering.
        mesh.getTransforms().add(new Rotate(90, Rotate.Z_AXIS));
        mesh.setMaterial(material);
        mesh.setMouseTransparent(true);
        return mesh;
    }

    private static TriangleMesh readMesh(String name, String expectedText) {
        String resource = "/com/commonwealthrobotics/workplane-labels/" + name + ".mesh";
        InputStream stream = WorkplaneReference.class.getResourceAsStream(resource);
        if (stream == null) throw new IllegalStateException("Missing vector label: " + resource);
        try (DataInputStream input = new DataInputStream(stream)) {
            if (input.readInt() != 0x43574631 || !expectedText.equals(input.readUTF()))
                throw new IOException("Wrong vector label header: " + name);
            TriangleMesh mesh = new TriangleMesh();
            mesh.getPoints().setAll(floats(input));
            mesh.getTexCoords().setAll(floats(input));
            int[] faces = new int[count(input)];
            for (int i = 0; i < faces.length; i++) faces[i] = input.readInt();
            mesh.getFaces().setAll(faces);
            if (input.read() != -1) throw new IOException("Trailing vector label data: " + name);
            return mesh;
        } catch (IOException error) { throw new UncheckedIOException(error); }
    }

    private static float[] floats(DataInputStream input) throws IOException {
        float[] values = new float[count(input)];
        for (int i = 0; i < values.length; i++) {
            values[i] = input.readFloat();
            if (!Float.isFinite(values[i])) throw new IOException("Invalid glyph coordinate");
        }
        return values;
    }

    private static int count(DataInputStream input) throws IOException {
        int count = input.readInt();
        if (count <= 0 || count > 1_000_000) throw new IOException("Invalid vector label length");
        return count;
    }
}
