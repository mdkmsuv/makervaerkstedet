package com.commonwealthrobotics.kloster;

import java.awt.BasicStroke;
import java.awt.Shape;
import java.awt.geom.Area;
import java.awt.geom.Ellipse2D;
import java.awt.geom.Path2D;
import java.awt.geom.PathIterator;
import java.awt.geom.Point2D;
import java.awt.geom.Rectangle2D;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.HashMap;
import java.util.Map;
import org.poly2tri.Poly2Tri;
import org.poly2tri.geometry.polygon.Polygon;
import org.poly2tri.geometry.polygon.PolygonPoint;

/** Millimetres throughout. Area resolves overlapping strokes before triangulation. */
public final class SketchGeometry {
    public static final double PLATE = 180;
    public static final double FLATNESS = 0.08;

    public static final class Mark {
        public String tool;
        public boolean cut;
        public double width;
        public List<double[]> points = new ArrayList<>();
        public Mark() {}
        public Mark(String tool, boolean cut, double width) {
            this.tool = tool; this.cut = cut; this.width = width;
        }
    }

    public static final class Drawing {
        public int version = 1;
        public double height = 3;
        public List<Mark> marks = new ArrayList<>();
    }

    public record Triangle(double[] a, double[] b, double[] c) {}

    public static Shape shape(Mark mark) {
        if (mark.points.isEmpty()) return new Path2D.Double();
        double[] first = mark.points.getFirst(), last = mark.points.getLast();
        double x = Math.min(first[0], last[0]), y = Math.min(first[1], last[1]);
        double w = Math.abs(last[0] - first[0]), h = Math.abs(last[1] - first[1]);
        if ("Rektangel".equals(mark.tool)) return new Rectangle2D.Double(x, y, w, h);
        if ("Ellipse".equals(mark.tool)) return new Ellipse2D.Double(x, y, w, h);
        Path2D.Double path = new Path2D.Double(Path2D.WIND_EVEN_ODD);
        path.moveTo(first[0], first[1]);
        for (int i = 1; i < mark.points.size(); i++) {
            double[] p = mark.points.get(i); path.lineTo(p[0], p[1]);
        }
        if ("Polygon".equals(mark.tool)) { path.closePath(); return path; }
        if (mark.points.size() == 1) return new Ellipse2D.Double(first[0] - mark.width / 2,
                first[1] - mark.width / 2, mark.width, mark.width);
        return new BasicStroke((float) mark.width, BasicStroke.CAP_ROUND,
                BasicStroke.JOIN_ROUND).createStrokedShape(path);
    }

    public static Area area(List<Mark> marks) {
        Area result = new Area();
        for (Mark mark : marks) {
            Area next = new Area(shape(mark));
            if (mark.cut) result.subtract(next); else result.add(next);
        }
        result.intersect(new Area(new Rectangle2D.Double(0, 0, PLATE, PLATE)));
        return result;
    }

    public static void validate(Drawing drawing) {
        if (drawing == null || drawing.version != 1 || drawing.marks == null
                || drawing.marks.size() > 2000 || !Double.isFinite(drawing.height)
                || drawing.height < 0.4 || drawing.height > 180)
            throw new IllegalArgumentException("Skitsefilen eller højden er ugyldig.");
        int count = 0;
        for (Mark mark : drawing.marks) {
            if (mark == null || mark.tool == null || !List.of("Rektangel", "Ellipse", "Polygon", "Frihånd", "Viskelæder").contains(mark.tool)
                    || !Double.isFinite(mark.width) || mark.width < 0.4 || mark.width > 30 || mark.points == null)
                throw new IllegalArgumentException("Skitsefilen indeholder et ugyldigt værktøj.");
            for (double[] point : mark.points) {
                if (++count > 50000 || point == null || point.length != 2
                        || !Double.isFinite(point[0]) || !Double.isFinite(point[1])
                        || point[0] < 0 || point[0] > PLATE || point[1] < 0 || point[1] > PLATE)
                    throw new IllegalArgumentException("Skitsefilen indeholder ugyldige punkter.");
            }
        }
    }

    private static final class Ring {
        final List<Point2D.Double> points;
        final Path2D.Double path = new Path2D.Double();
        final double size;
        int parent = -1, depth;
        Ring(List<Point2D.Double> points) {
            this.points = points;
            path.moveTo(points.getFirst().x, points.getFirst().y);
            for (int i = 1; i < points.size(); i++) path.lineTo(points.get(i).x, points.get(i).y);
            path.closePath(); size = Math.abs(signedArea(points));
        }
        Polygon polygon() {
            List<PolygonPoint> vertices = new ArrayList<>();
            for (Point2D.Double p : points) vertices.add(new PolygonPoint(p.x, p.y));
            return new Polygon(vertices);
        }
    }

    private static double signedArea(List<Point2D.Double> points) {
        double sum = 0;
        for (int i = 0; i < points.size(); i++) {
            Point2D.Double a = points.get(i), b = points.get((i + 1) % points.size());
            sum += a.x * b.y - b.x * a.y;
        }
        return sum / 2;
    }

    public static List<List<Point2D.Double>> contours(Area area) {
        List<List<Point2D.Double>> result = new ArrayList<>();
        List<Point2D.Double> current = null;
        double[] p = new double[6];
        for (PathIterator it = area.getPathIterator(null, FLATNESS); !it.isDone(); it.next()) {
            int type = it.currentSegment(p);
            if (type == PathIterator.SEG_MOVETO) { current = new ArrayList<>(); result.add(current); }
            if (type == PathIterator.SEG_MOVETO || type == PathIterator.SEG_LINETO) {
                Point2D.Double next = new Point2D.Double(p[0], p[1]);
                if (current.isEmpty() || current.getLast().distance(next) > 1e-7) current.add(next);
            }
        }
        for (List<Point2D.Double> ring : result) {
            if (ring.size() > 1 && ring.getFirst().distance(ring.getLast()) < 1e-7) ring.removeLast();
            // Eliminate only exactly collinear vertices; preserve all drawing dimensions.
            boolean changed = true;
            while (changed && ring.size() > 3) {
                changed = false;
                for (int i = 0; i < ring.size(); i++) {
                    Point2D.Double a = ring.get((i + ring.size() - 1) % ring.size());
                    Point2D.Double b = ring.get(i), c = ring.get((i + 1) % ring.size());
                    if (Math.abs((b.x-a.x)*(c.y-b.y)-(b.y-a.y)*(c.x-b.x)) < 1e-9) {
                        ring.remove(i); changed = true; break;
                    }
                }
            }
        }
        result.removeIf(ring -> ring.size() < 3 || Math.abs(signedArea(ring)) < 1e-6);
        return result;
    }

    public static List<Triangle> extrude(Area area, double height) {
        if (area.isEmpty()) throw new IllegalArgumentException("Tegn først en figur på pladen.");
        if (!Double.isFinite(height) || height < 0.4 || height > 180)
            throw new IllegalArgumentException("Højden skal være mellem 0,4 og 180 mm.");
        List<Ring> rings = new ArrayList<>();
        for (List<Point2D.Double> points : contours(area)) rings.add(new Ring(points));
        for (int i = 0; i < rings.size(); i++) {
            Ring r = rings.get(i); double smallest = Double.POSITIVE_INFINITY;
            for (int j = 0; j < rings.size(); j++) {
                Ring outer = rings.get(j);
                if (i != j && outer.size > r.size && outer.size < smallest && outer.path.contains(r.points.getFirst())) {
                    r.parent = j; smallest = outer.size;
                }
            }
        }
        for (Ring r : rings) {
            for (int p = r.parent; p >= 0; p = rings.get(p).parent) r.depth++;
            // In drawing coordinates outer boundaries CCW, holes CW. Y is reflected below.
            if ((signedArea(r.points) > 0) != (r.depth % 2 == 0)) Collections.reverse(r.points);
        }
        List<Triangle> mesh = new ArrayList<>();
        for (int i = 0; i < rings.size(); i++) {
            Ring r = rings.get(i);
            if (r.depth % 2 == 0) {
                Polygon polygon = r.polygon();
                for (Ring hole : rings) if (hole.parent == i) polygon.addHole(hole.polygon());
                Poly2Tri.triangulate(polygon);
                for (var tri : polygon.getTriangles()) {
                    double[] a = point(tri.points[0].getX(), tri.points[0].getY(), height);
                    double[] b = point(tri.points[1].getX(), tri.points[1].getY(), height);
                    double[] c = point(tri.points[2].getX(), tri.points[2].getY(), height);
                    if ((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]) < 0) {
                        double[] swap = b; b = c; c = swap;
                    }
                    mesh.add(new Triangle(a,b,c));
                    mesh.add(new Triangle(new double[]{a[0],a[1],0},new double[]{c[0],c[1],0},new double[]{b[0],b[1],0}));
                }
            }
            for (int j = 0; j < r.points.size(); j++) {
                Point2D.Double a = r.points.get(j), b = r.points.get((j + 1) % r.points.size());
                double[] a0 = point(a.x,a.y,0), a1 = point(a.x,a.y,height);
                double[] b0 = point(b.x,b.y,0), b1 = point(b.x,b.y,height);
                mesh.add(new Triangle(a0,a1,b1)); mesh.add(new Triangle(a0,b1,b0));
            }
        }
        if (mesh.isEmpty()) throw new IllegalArgumentException("Figuren er for lille. Tegn en større, lukket figur.");
        validateMesh(mesh);
        return mesh;
    }

    /** Reject touching/degenerate contours rather than exporting an unprintable surface. */
    private static void validateMesh(List<Triangle> mesh) {
        Map<String, int[]> edges = new HashMap<>();
        for (Triangle tri : mesh) {
            List<double[]> points = List.of(tri.a(), tri.b(), tri.c());
            for (int i = 0; i < 3; i++) {
                String a = vertexKey(points.get(i)), b = vertexKey(points.get((i + 1) % 3));
                if (a.equals(b)) throw new IllegalArgumentException("En detalje er for lille. Gør den lidt større.");
                boolean forward = a.compareTo(b) < 0;
                String key = forward ? a + "|" + b : b + "|" + a;
                int[] counts = edges.computeIfAbsent(key, k -> new int[2]);
                counts[0]++; counts[1] += forward ? 1 : -1;
            }
        }
        for (int[] counts : edges.values()) {
            if (counts[0] != 2 || counts[1] != 0)
                throw new IllegalArgumentException("To kanter mødes kun i et punkt. Lad dem overlappe lidt, eller lav afstand mellem dem.");
        }
    }

    private static String vertexKey(double[] p) {
        return Float.floatToIntBits((float)(p[0] + 0.0)) + ","
                + Float.floatToIntBits((float)(p[1] + 0.0)) + "," + Float.floatToIntBits((float)(p[2] + 0.0));
    }

    private static double[] point(double x, double y, double z) { return new double[]{x,PLATE-y,z}; }

    public static void writeStl(Path destination, List<Triangle> mesh) throws IOException {
        ByteBuffer out = ByteBuffer.allocate(84 + mesh.size() * 50).order(ByteOrder.LITTLE_ENDIAN);
        out.put("Makerværkstedet - CaDoodle skitse (mm)".getBytes(java.nio.charset.StandardCharsets.US_ASCII));
        out.position(80); out.putInt(mesh.size());
        for (Triangle tri : mesh) {
            double[] a = tri.a(), b = tri.b(), c = tri.c();
            double nx=(b[1]-a[1])*(c[2]-a[2])-(b[2]-a[2])*(c[1]-a[1]);
            double ny=(b[2]-a[2])*(c[0]-a[0])-(b[0]-a[0])*(c[2]-a[2]);
            double nz=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]);
            double length=Math.sqrt(nx*nx+ny*ny+nz*nz);
            out.putFloat((float)(nx/length)); out.putFloat((float)(ny/length)); out.putFloat((float)(nz/length));
            for (double[] p : List.of(a,b,c)) for (double coordinate : p) out.putFloat((float)coordinate);
            out.putShort((short)0);
        }
        Files.write(destination, out.array());
    }
}
