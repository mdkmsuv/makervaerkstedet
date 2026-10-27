import com.commonwealthrobotics.kloster.PrintModel;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Native-backend regression for PrintModel's direct triangle STL writer. */
public final class PrintTriangleExportTest {
    private static final Pattern FACET = Pattern.compile(
            "facet normal\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)\\s+outer loop\\s+"
                    + "vertex\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)\\s+"
                    + "vertex\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)\\s+"
                    + "vertex\\s+([^\\s]+)\\s+([^\\s]+)\\s+([^\\s]+)\\s+endloop",
            Pattern.MULTILINE);

    public static void main(String[] args) throws Exception {
        CSG.setDefaultOptType(CSG.OptType.Manifold3d);
        if (CSG.getManifold() == null) {
            System.out.println("SKIP: native Manifold3d backend unavailable");
            return;
        }

        CSG outer = new Cube(40, 30, 20).toCSG();
        CSG cutter = new Cube(12, 10, 30).toCSG().movez(1).setIsHole(true);
        CSG drilled = outer.difference(cutter);
        CSG hidden = new Cube(140).toCSG().setIsHide(true);
        CSG grouped = new Cube(140).toCSG().addGroupMembership("hidden-group");
        CSG hole = new Cube(140).toCSG().setIsHole(true);

        Path folder = Files.createTempDirectory("print-triangle-test-");
        Path stl = folder.resolve("drilled.stl");
        try {
            PrintModel.write(stl, List.of(drilled, hidden, grouped, hole));
            String text = Files.readString(stl);
            List<double[][]> triangles = parse(text);
            require(!triangles.isEmpty(), "native result exported no facets");
            require(triangles.size() == count(text, "facet normal"),
                    "STL facet count does not match serialized facet records");
            double volume = 0;
            for (double[][] triangle : triangles) {
                double[] a = triangle[0], b = triangle[1], c = triangle[2];
                double[] cross = cross(subtract(b, a), subtract(c, a));
                double nx = cross[0], ny = cross[1], nz = cross[2];
                double area2 = Math.sqrt(nx * nx + ny * ny + nz * nz);
                require(Double.isFinite(area2) && area2 > 1e-9, "zero-area or non-finite STL facet");
                for (double[] point : triangle) for (double coordinate : point)
                    require(Double.isFinite(coordinate) && Math.abs(coordinate) <= 175.0,
                            "STL coordinate exceeds finite 175 mm bound");
                volume += dot(a, cross) / 6.0;
            }
            volume = Math.abs(volume);
            // 40*30*20 minus a through 12*10 hole: 24000 - 2400 = 21600.
            require(Math.abs(volume - 21600.0) < 2.0,
                    "STL volume differs from drilled native geometry: " + volume);
            require(!text.contains("140.0") && !text.contains("-70.0"),
                    "hidden, hole and grouped members leaked into STL");
            System.out.println("PASS: native direct-triangle STL has finite facets, unit normals, drilled volume and filtered members");
        } finally {
            Files.deleteIfExists(stl);
            Files.deleteIfExists(folder);
        }
    }

    private static List<double[][]> parse(String text) {
        List<double[][]> result = new ArrayList<>();
        Matcher matcher = FACET.matcher(text);
        while (matcher.find()) {
            double nx = number(matcher, 1), ny = number(matcher, 2), nz = number(matcher, 3);
            double length = Math.sqrt(nx * nx + ny * ny + nz * nz);
            require(Double.isFinite(length) && Math.abs(length - 1.0) < 1e-6,
                    "facet normal is not finite unit length");
            result.add(new double[][] {
                    {number(matcher, 4), number(matcher, 5), number(matcher, 6)},
                    {number(matcher, 7), number(matcher, 8), number(matcher, 9)},
                    {number(matcher, 10), number(matcher, 11), number(matcher, 12)}
            });
            double[][] points = result.get(result.size() - 1);
            double[] edgeCross = cross(subtract(points[1], points[0]), subtract(points[2], points[0]));
            double edgeLength = Math.sqrt(dot(edgeCross, edgeCross));
            require(Math.abs((nx * edgeCross[0] + ny * edgeCross[1] + nz * edgeCross[2]))
                            / edgeLength > 0.999999,
                    "facet normal is not aligned with triangle orientation");
        }
        return result;
    }

    private static double number(Matcher matcher, int group) {
        return Double.parseDouble(matcher.group(group));
    }

    private static double[] cross(double[] a, double[] b) {
        return new double[] {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0]};
    }

    private static double[] subtract(double[] a, double[] b) {
        return new double[] {a[0] - b[0], a[1] - b[1], a[2] - b[2]};
    }

    private static double dot(double[] a, double[] b) {
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    }

    private static int count(String text, String needle) {
        int total = 0, offset = 0;
        while ((offset = text.indexOf(needle, offset)) >= 0) {
            total++;
            offset += needle.length();
        }
        return total;
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
