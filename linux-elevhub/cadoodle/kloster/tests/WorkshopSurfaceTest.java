package com.commonwealthrobotics.kloster;

import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.Sphere;
import javafx.scene.paint.Color;
import javafx.scene.paint.PhongMaterial;
import javafx.scene.shape.MeshView;
import javafx.scene.shape.TriangleMesh;
import javafx.scene.shape.VertexFormat;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Headless checks for render-only normals and material preservation. */
public final class WorkshopSurfaceTest {
    private static final double EPSILON = 1e-4;

    private WorkshopSurfaceTest() { }

    public static void main(String[] args) {
        checkCubeGeometryIsPreserved();
        checkSphereGetsSmoothNormals();
        checkNinetyDegreeCreaseRemainsSharp();
        checkMaterialFinishFollowsReplacement();
        System.out.println("WorkshopSurfaceTest: passed");
    }

    private static void checkCubeGeometryIsPreserved() {
        CSG cube = new Cube(20, 20, 20).toCSG();
        MeshView view = cube.getMesh();
        TriangleMesh original = (TriangleMesh) view.getMesh();
        float[] points = original.getPoints().toArray(new float[original.getPoints().size()]);
        int[] faces = original.getFaces().toArray(new int[original.getFaces().size()]);
        var bounds = view.getBoundsInLocal();
        WorkshopSurface.prepare(view);
        TriangleMesh render = (TriangleMesh) view.getMesh();
        require(render.getVertexFormat() == VertexFormat.POINT_NORMAL_TEXCOORD, "cube render format");
        require(equal(points, original.getPoints().toArray(new float[original.getPoints().size()])),
                "cube source points changed");
        require(equal(faces, original.getFaces().toArray(new int[original.getFaces().size()])),
                "cube source faces changed");
        require(equal(points, render.getPoints().toArray(new float[render.getPoints().size()])),
                "cube render points changed");
        require(Math.abs(bounds.getWidth() - view.getBoundsInLocal().getWidth()) < EPSILON
                        && Math.abs(bounds.getHeight() - view.getBoundsInLocal().getHeight()) < EPSILON
                        && Math.abs(bounds.getDepth() - view.getBoundsInLocal().getDepth()) < EPSILON,
                "cube bounds changed");
        require(pointIndicesEqual(faces, render.getFaces().toArray(new int[render.getFaces().size()])),
                "cube triangle point indices changed");
    }

    private static void checkSphereGetsSmoothNormals() {
        CSG sphere = new Sphere(10, 24, 12).toCSG();
        String stlBefore = sphere.toStlString();
        MeshView view = sphere.getMesh();
        WorkshopSurface.prepare(view);
        TriangleMesh mesh = (TriangleMesh) view.getMesh();
        require(mesh.getVertexFormat() == VertexFormat.POINT_NORMAL_TEXCOORD, "sphere render format");
        float[] points = mesh.getPoints().toArray(new float[mesh.getPoints().size()]);
        float[] normals = mesh.getNormals().toArray(new float[mesh.getNormals().size()]);
        Map<Key, List<Integer>> normalGroups = new HashMap<>();
        int[] renderFaces = mesh.getFaces().toArray(new int[mesh.getFaces().size()]);
        for (int i = 0; i < renderFaces.length; i += 9) {
            for (int corner = 0; corner < 3; corner++) {
                int point = renderFaces[i + corner * 3];
                int normal = renderFaces[i + corner * 3 + 1] * 3;
                Key key = new Key(points[point * 3], points[point * 3 + 1], points[point * 3 + 2]);
                normalGroups.computeIfAbsent(key, ignored -> new ArrayList<>()).add(normal);
            }
        }
        int sharedKeys = 0;
        int smoothKeys = 0;
        int checkedNormals = 0;
        int radialNormals = 0;
        for (Map.Entry<Key, List<Integer>> entry : normalGroups.entrySet()) {
            List<Integer> group = entry.getValue();
            if (group.size() < 2) continue;
            sharedKeys++;
            boolean smooth = false;
            for (int i = 0; i < group.size(); i++) {
                int normal = group.get(i);
                if (dotToPosition(normals, normal, entry.getKey()) > 0.90) radialNormals++;
                checkedNormals++;
                for (int j = i + 1; j < group.size(); j++) {
                    if (dot(normals, normal, group.get(j)) > 0.98) smooth = true;
                }
            }
            if (smooth) smoothKeys++;
        }
        require(sharedKeys >= 20, "sphere has too few shared vertex groups: " + sharedKeys);
        require(smoothKeys >= 20, "sphere has too few blended groups: " + smoothKeys);
        require(checkedNormals > 100 && radialNormals * 100 >= checkedNormals * 90,
                "sphere normals are not radial: " + radialNormals + "/" + checkedNormals);
        require(stlBefore.equals(sphere.toStlString()), "sphere STL geometry changed");
    }

    private static void checkNinetyDegreeCreaseRemainsSharp() {
        TriangleMesh source = new TriangleMesh(VertexFormat.POINT_TEXCOORD);
        source.getPoints().setAll(0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1);
        source.getTexCoords().setAll(0, 0);
        source.getFaces().setAll(0, 0, 1, 0, 2, 0, 0, 0, 3, 0, 1, 0);
        MeshView view = new MeshView(source);
        WorkshopSurface.prepare(view);
        TriangleMesh render = (TriangleMesh) view.getMesh();
        int[] faces = render.getFaces().toArray(new int[render.getFaces().size()]);
        float[] normals = render.getNormals().toArray(new float[render.getNormals().size()]);
        int firstNormal = faces[1] * 3;
        int secondNormal = faces[10] * 3;
        require(dot(normals, firstNormal, secondNormal) < 0.1, "90 degree crease was blended");
    }

    private static void checkMaterialFinishFollowsReplacement() {
        MeshView view = new MeshView(new TriangleMesh());
        view.setMaterial(new PhongMaterial(Color.RED));
        WorkshopSurface.prepare(view);
        PhongMaterial first = (PhongMaterial) view.getMaterial();
        require(Math.abs(first.getSpecularColor().getRed() - 0.12) < EPSILON
                        && Math.abs(first.getSpecularPower() - 24) < EPSILON, "initial matte finish");
        PhongMaterial replacement = new PhongMaterial(Color.BLUE);
        view.setMaterial(replacement);
        require(Math.abs(replacement.getSpecularColor().getGreen() - 0.12) < EPSILON
                        && Math.abs(replacement.getSpecularPower() - 24) < EPSILON, "replacement matte finish");
    }

    private static boolean pointIndicesEqual(int[] source, int[] render) {
        if (render.length != source.length / 2 * 3) return false;
        for (int face = 0; face < source.length / 6; face++) {
            for (int corner = 0; corner < 3; corner++) {
                if (source[face * 6 + corner * 2] != render[face * 9 + corner * 3]) return false;
            }
        }
        return true;
    }

    private static boolean equal(float[] a, float[] b) {
        if (a.length != b.length) return false;
        for (int i = 0; i < a.length; i++) if (Math.abs(a[i] - b[i]) > EPSILON) return false;
        return true;
    }

    private static boolean equal(int[] a, int[] b) {
        if (a.length != b.length) return false;
        for (int i = 0; i < a.length; i++) if (a[i] != b[i]) return false;
        return true;
    }

    private static double dot(float[] normals, int a, int b) {
        return normals[a] * normals[b] + normals[a + 1] * normals[b + 1] + normals[a + 2] * normals[b + 2];
    }

    private static double dotToPosition(float[] normals, int normal, Key position) {
        double length = Math.sqrt(position.x * position.x + position.y * position.y + position.z * position.z);
        if (length < EPSILON) return 1.0;
        return (normals[normal] * position.x + normals[normal + 1] * position.y
                + normals[normal + 2] * position.z) / length;
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private record Key(float x, float y, float z) { }
}
