package com.commonwealthrobotics.kloster;

import eu.mihosoft.vrl.v3d.CSG;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.List;

/** One STL, retaining the arrangement of every printable part and omitting colours. */
public final class PrintModel {
    /** Leave a small margin below the A1 mini's physical 180 mm volume. */
    private static final double STUDENT_LIMIT_MM = 175.0;

    public static void write(Path target, List<CSG> shapes) throws IOException {
        long bytes = 0;
        int facetsWritten = 0;
        try {
            // Check the same combined bounding-box dimensions that the queue
            // checks after STL inspection.  Do this before opening the file,
            // so an over-sized pupil model never leaves a partial STL.
            double[] low = {Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY, Double.POSITIVE_INFINITY};
            double[] high = {Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY, Double.NEGATIVE_INFINITY};
            for (CSG shape : shapes) {
                if (shape.isHide() || shape.isHole() || (shape.isInGroup() && !shape.isAlwaysShow())) continue;
                for (long index : shape.getTriangles()) {
                        double[] coordinates = point(shape, index);
                        for (int axis = 0; axis < coordinates.length; axis++) {
                            if (!Double.isFinite(coordinates[axis]))
                                throw new IOException("Modellen indeholder ugyldige koordinater.");
                            low[axis] = Math.min(low[axis], coordinates[axis]);
                            high[axis] = Math.max(high[axis], coordinates[axis]);
                        }
                }
            }
            for (int axis = 0; axis < 3; axis++) {
                if (high[axis] - low[axis] > STUDENT_LIMIT_MM + 1e-9)
                    throw new IOException("Modellen er større end elevgrænsen på 175 × 175 × 175 mm. Gør den mindre, og prøv igen.");
            }
            try (OutputStream output = new BufferedOutputStream(Files.newOutputStream(target))) {
                output.write("solid Kloster\n".getBytes(StandardCharsets.US_ASCII));
                for (CSG shape : shapes) {
                    if (shape.isHide() || shape.isHole() || (shape.isInGroup() && !shape.isAlwaysShow())) continue;
                    // Keep the backend's triangulation. Reconstructing Polygon
                    // normals can fail on near-collinear boolean seam triangles.
                    long[] triangles = shape.getTriangles();
                    if (triangles.length % 3 != 0) throw new IOException("Modellen har ugyldige flader.");
                    for (int i = 0; i < triangles.length; i += 3) {
                        double[] a = point(shape, triangles[i]), b = point(shape, triangles[i + 1]), c = point(shape, triangles[i + 2]);
                        String triangle = facet(a, b, c);
                        if (triangle == null) continue; // Zero-area faces carry no printable surface.
                        byte[] facets = triangle.getBytes(StandardCharsets.US_ASCII);
                        bytes += facets.length;
                        if (bytes > 25 * 1024 * 1024 - 100) throw new IOException("Modellen er for detaljeret. Gør den enklere, og prøv igen.");
                        output.write(facets);
                        facetsWritten++;
                    }
                }
                output.write("endsolid Kloster\n".getBytes(StandardCharsets.US_ASCII));
            }
        } catch (Exception error) { Files.deleteIfExists(target); throw error; }
        if (facetsWritten == 0) {
            Files.deleteIfExists(target);
            throw new IOException("Tegningen er tom. Tilføj en synlig, solid figur, før du sender til print.");
        }
    }

    private static double[] point(CSG shape, long vertex) {
        int index = Math.toIntExact(vertex);
        return new double[] {shape.getVertex_X(index), shape.getVertex_Y(index), shape.getVertex_Z(index)};
    }

    private static String facet(double[] a, double[] b, double[] c) {
        double ux = b[0] - a[0], uy = b[1] - a[1], uz = b[2] - a[2];
        double vx = c[0] - a[0], vy = c[1] - a[1], vz = c[2] - a[2];
        double nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
        double length = Math.sqrt(nx * nx + ny * ny + nz * nz);
        if (length <= 1e-12) return null;
        return "facet normal " + nx / length + " " + ny / length + " " + nz / length + "\nouter loop\n"
                + vertex(a) + vertex(b) + vertex(c) + "endloop\nendfacet\n";
    }

    private static String vertex(double[] point) {
        return "vertex " + point[0] + " " + point[1] + " " + point[2] + "\n";
    }
}
