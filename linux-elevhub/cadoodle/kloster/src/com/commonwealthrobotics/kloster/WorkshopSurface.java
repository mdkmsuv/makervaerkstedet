package com.commonwealthrobotics.kloster;

import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.Mesh;
import javafx.scene.shape.MeshView;
import javafx.scene.shape.TriangleMesh;
import javafx.scene.shape.VertexFormat;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Render-only surface preparation; CSG and exported geometry are never modified. */
public final class WorkshopSurface {
    private static final String PREPARED = WorkshopSurface.class.getName() + ".prepared";
    private static final String MATERIAL_LISTENER = WorkshopSurface.class.getName() + ".materialListener";
    private static final double CREASE_COSINE = Math.cos(Math.toRadians(35.0));
    private static final double EPSILON = 1e-12;
    private static final int MAX_TRIANGLES = 100_000;

    private WorkshopSurface() { }

    /**
     * Applies matte highlights and, for a suitable triangle mesh, render-only corner normals.
     * The supplied MeshView object, event handlers, transforms, material diffuse color and
     * texture references remain owned by the caller.
     */
    public static void prepare(MeshView view) {
        if (view == null) return;
        installMaterialListener(view);
        if (view.getProperties().putIfAbsent(PREPARED, Boolean.TRUE) != null) return;

        Mesh source = view.getMesh();
        if (!(source instanceof TriangleMesh original)) return;
        if (original.getVertexFormat() == VertexFormat.POINT_NORMAL_TEXCOORD) return;
        if (original.getVertexFormat() != VertexFormat.POINT_TEXCOORD) return;

        int faceElementSize = original.getFaceElementSize();
        int faceCount = faceElementSize == 6 ? original.getFaces().size() / faceElementSize : 0;
        if (faceCount == 0 || faceCount > MAX_TRIANGLES) return;

        float[] points = original.getPoints().toArray(new float[original.getPoints().size()]);
        float[] texCoords = original.getTexCoords().toArray(new float[original.getTexCoords().size()]);
        int[] faces = original.getFaces().toArray(new int[original.getFaces().size()]);
        if (!validFaces(points, texCoords, faces, faceCount)) return;

        List<Face> faceData = new ArrayList<>(faceCount);
        Map<PointKey, List<Corner>> byPosition = new HashMap<>();
        for (int faceIndex = 0; faceIndex < faceCount; faceIndex++) {
            int offset = faceIndex * 6;
            int p0 = faces[offset], p1 = faces[offset + 2], p2 = faces[offset + 4];
            Vector a = point(points, p0), b = point(points, p1), c = point(points, p2);
            Vector cross = b.subtract(a).cross(c.subtract(a));
            double length = cross.length();
            Vector normal = length > EPSILON ? cross.scale(1.0 / length) : null;
            Face face = new Face(normal, length);
            faceData.add(face);
            int[] pointIndices = {p0, p1, p2};
            for (int cornerIndex = 0; cornerIndex < 3; cornerIndex++) {
                Corner corner = new Corner(face, faceIndex, cornerIndex, pointIndices[cornerIndex]);
                face.corners[cornerIndex] = corner;
                byPosition.computeIfAbsent(key(points, pointIndices[cornerIndex]), ignored -> new ArrayList<>())
                        .add(corner);
            }
        }

        float[] normals = new float[faceCount * 9];
        int[] renderFaces = new int[faceCount * 9];
        for (int faceIndex = 0; faceIndex < faceCount; faceIndex++) {
            Face face = faceData.get(faceIndex);
            for (int cornerIndex = 0; cornerIndex < 3; cornerIndex++) {
                Corner corner = face.corners[cornerIndex];
                Vector normal = blendedNormal(corner, byPosition.get(key(points, corner.pointIndex)));
                int normalIndex = (faceIndex * 3 + cornerIndex) * 3;
                normals[normalIndex] = (float) normal.x;
                normals[normalIndex + 1] = (float) normal.y;
                normals[normalIndex + 2] = (float) normal.z;
                int sourceOffset = faceIndex * 6 + cornerIndex * 2;
                int renderOffset = faceIndex * 9 + cornerIndex * 3;
                renderFaces[renderOffset] = faces[sourceOffset];
                renderFaces[renderOffset + 1] = faceIndex * 3 + cornerIndex;
                renderFaces[renderOffset + 2] = faces[sourceOffset + 1];
            }
        }

        TriangleMesh renderMesh = new TriangleMesh(VertexFormat.POINT_NORMAL_TEXCOORD);
        renderMesh.getPoints().setAll(points);
        renderMesh.getNormals().setAll(normals);
        renderMesh.getTexCoords().setAll(texCoords);
        renderMesh.getFaces().setAll(renderFaces);
        view.setMesh(renderMesh);
    }

    private static void installMaterialListener(MeshView view) {
        if (view.getProperties().putIfAbsent(MATERIAL_LISTENER, Boolean.TRUE) == null) {
            view.materialProperty().addListener((observable, oldMaterial, newMaterial) -> matte(newMaterial));
        }
        matte(view.getMaterial());
    }

    private static void matte(javafx.scene.paint.Material material) {
        if (material instanceof PhongMaterial phong) {
            phong.setSpecularColor(Color.color(0.12, 0.12, 0.12));
            phong.setSpecularPower(24.0);
        }
    }

    private static boolean validFaces(float[] points, float[] texCoords, int[] faces, int faceCount) {
        for (int faceIndex = 0; faceIndex < faceCount; faceIndex++) {
            int offset = faceIndex * 6;
            for (int corner = 0; corner < 3; corner++) {
                int pointIndex = faces[offset + corner * 2];
                int texIndex = faces[offset + corner * 2 + 1];
                if (pointIndex < 0 || pointIndex * 3 + 2 >= points.length
                        || texIndex < 0 || texIndex * 2 + 1 >= texCoords.length) return false;
            }
        }
        return true;
    }

    private static Vector blendedNormal(Corner corner, List<Corner> neighbors) {
        Vector target = corner.face.normal;
        // Coincident duplicate faces in a damaged import must not turn this
        // pass into quadratic work. Keep a flat normal for pathological fans.
        if (neighbors.size() > 128) return target == null ? new Vector(0, 0, 1) : target;
        if (target == null) {
            for (Corner neighbor : neighbors) {
                if (neighbor.face.normal != null) return neighbor.face.normal;
            }
            return new Vector(0, 0, 1);
        }
        Vector sum = new Vector(0, 0, 0);
        for (Corner neighbor : neighbors) {
            if (neighbor.face.normal != null && target.dot(neighbor.face.normal) >= CREASE_COSINE) {
                sum = sum.add(neighbor.face.normal.scale(Math.max(neighbor.face.area, EPSILON)));
            }
        }
        double length = sum.length();
        return length > EPSILON ? sum.scale(1.0 / length) : target;
    }

    private static Vector point(float[] points, int index) {
        int offset = index * 3;
        return new Vector(points[offset], points[offset + 1], points[offset + 2]);
    }

    private static PointKey key(float[] points, int index) {
        int offset = index * 3;
        return new PointKey(bits(points[offset]), bits(points[offset + 1]), bits(points[offset + 2]));
    }

    private static int bits(float value) {
        // JavaFX meshes can spell the same seam coordinate as either signed zero.
        return Float.floatToIntBits(value == 0.0f ? 0.0f : value);
    }

    private record PointKey(int x, int y, int z) { }

    private static final class Face {
        private final Vector normal;
        private final double area;
        private final Corner[] corners = new Corner[3];

        private Face(Vector normal, double area) {
            this.normal = normal;
            this.area = area;
        }
    }

    private record Corner(Face face, int faceIndex, int cornerIndex, int pointIndex) { }

    private record Vector(double x, double y, double z) {
        private Vector add(Vector other) { return new Vector(x + other.x, y + other.y, z + other.z); }
        private Vector subtract(Vector other) { return new Vector(x - other.x, y - other.y, z - other.z); }
        private Vector scale(double amount) { return new Vector(x * amount, y * amount, z * amount); }
        private double dot(Vector other) { return x * other.x + y * other.y + z * other.z; }
        private double length() { return Math.sqrt(x * x + y * y + z * z); }
        private Vector cross(Vector other) {
            return new Vector(y * other.z - z * other.y, z * other.x - x * other.z,
                    x * other.y - y * other.x);
        }
    }
}
