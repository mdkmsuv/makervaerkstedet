import com.commonwealthrobotics.kloster.LearningModels;
import eu.mihosoft.vrl.v3d.CSG;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Structural checks for native, editable learning starters. */
public final class LearningModelsTest {
    private static final List<String> IDS = List.of("name-tag", "robot", "game-piece",
            "pencil-holder", "desk-sign", "open-box");

    public static void main(String[] args) {
        for (String id : IDS) check(id);
        checkPreviewDifferences();
        System.out.println("PASS: learning starters are native, named, bounded and non-empty.");
    }

    private static void check(String id) {
        List<CSG> parts = LearningModels.create(id, "TEST");
        if (parts.isEmpty()) throw new AssertionError(id + " is empty");
        Set<String> names = new HashSet<>();
        for (CSG part : parts) {
            if (part == null || part.getName() == null || part.getName().isBlank())
                throw new AssertionError(id + " has unnamed part");
            if (!names.add(part.getName())) throw new AssertionError(id + " has duplicate names");
            var bounds = part.getBounds();
            if (bounds.getMinZ() < -1e-6 || bounds.getMinX() < -1e-6 || bounds.getMinY() < -1e-6)
                throw new AssertionError(id + " is below or outside the plate");
            if (bounds.getMaxX() > 175.0001 || bounds.getMaxY() > 175.0001 || bounds.getMaxZ() > 175.0001)
                throw new AssertionError(id + " exceeds starter bounds");
            if (part.getTriangles() == null || part.getTriangles().length == 0)
                throw new AssertionError(id + " contains empty geometry");
        }
        if (id.equals("name-tag")) {
            List<CSG> shortText = LearningModels.create(id, "A");
            if (parts.get(2).getTriangles().length == shortText.get(2).getTriangles().length)
                throw new AssertionError("name-tag label did not change geometry");
        }
        if (id.equals("name-tag") || id.equals("pencil-holder") || id.equals("open-box")) {
            if (parts.get(1).isHole()) throw new AssertionError(id + " starter cutter is already a hole");
        }
        checkStableNamesAndCounts(id, parts);
        checkContinuity(id, parts);
        if (id.equals("name-tag") || id.equals("desk-sign")) {
            var base = parts.getFirst().getBounds();
            var text = parts.getLast().getBounds();
            if (text.getMinX() <= base.getMinX() || text.getMaxX() >= base.getMaxX()
                    || text.getMinY() <= base.getMinY() || text.getMaxY() >= base.getMaxY())
                throw new AssertionError(id + " letters extend beyond the plaque");
        }
    }

    private static void checkStableNamesAndCounts(String id, List<CSG> parts) {
        List<CSG> alternate = LearningModels.create(id, "LONGER LABEL");
        if (parts.size() != alternate.size()) throw new AssertionError(id + " part count depends on label");
        for (int i = 0; i < parts.size(); i++) {
            if (!parts.get(i).getName().equals(alternate.get(i).getName()))
                throw new AssertionError(id + " part name/index changed with label at " + i);
            if (parts.get(i).getName().length() > 32)
                throw new AssertionError(id + " part name is too long");
        }
    }

    private static void checkContinuity(String id, List<CSG> parts) {
        int[][] links = switch (id) {
            case "name-tag" -> new int[][]{{0, 2}};
            case "robot" -> new int[][]{{0, 1}, {1, 2}, {1, 3}, {1, 4}};
            case "game-piece" -> new int[][]{{0, 1}, {1, 2}};
            case "desk-sign" -> new int[][]{{0, 1}, {0, 2}, {0, 3}, {0, 4}, {0, 5}};
            default -> new int[0][0];
        };
        for (int[] link : links) {
            if (!overlap(parts.get(link[0]), parts.get(link[1])))
                throw new AssertionError(id + " disconnected parts " + link[0] + "/" + link[1]);
        }
    }

    private static boolean overlap(CSG a, CSG b) {
        var x = a.getBounds(); var y = b.getBounds();
        return Math.min(x.getMaxX(), y.getMaxX()) - Math.max(x.getMinX(), y.getMinX()) > 0.05
                && Math.min(x.getMaxY(), y.getMaxY()) - Math.max(x.getMinY(), y.getMinY()) > 0.05
                && Math.min(x.getMaxZ(), y.getMaxZ()) - Math.max(x.getMinZ(), y.getMinZ()) > 0.05;
    }

    private static void checkPreviewDifferences() {
        for (String id : List.of("name-tag", "pencil-holder", "open-box")) {
            List<CSG> raw = LearningModels.create(id, "TEST");
            List<CSG> preview = LearningModels.preview(id, "TEST");
            if (preview.size() != raw.size() - 1) throw new AssertionError(id + " preview did not assemble hole");
            double rawVolume = volume(raw.get(0));
            double previewVolume = volume(preview.get(0));
            if (!(previewVolume > 0 && rawVolume > 0 && previewVolume < rawVolume))
                throw new AssertionError(id + " preview cavity did not subtract volume");
            if (id.equals("pencil-holder")) {
                double expected = 60 * 35 * 40 - 2 * 25.5 * 29 * 37;
                if (Math.abs(previewVolume - expected) > 1.0)
                    throw new AssertionError("pencil-holder preview has wrong two-hole volume: "
                            + previewVolume + " expected=" + expected);
                if (preview.get(0).getTotalZ() < 39.99)
                    throw new AssertionError("pencil-holder preview did not raise outer height to 40 mm");
            }
        }
    }

    private static double volume(CSG csg) {
        double sum = 0;
        long[] triangles = csg.getTriangles();
        for (int i = 0; i < triangles.length; i += 3) {
            double[] a = point(csg, triangles[i]);
            double[] b = point(csg, triangles[i + 1]);
            double[] c = point(csg, triangles[i + 2]);
            sum += a[0] * (b[1] * c[2] - b[2] * c[1])
                    - a[1] * (b[0] * c[2] - b[2] * c[0])
                    + a[2] * (b[0] * c[1] - b[1] * c[0]);
        }
        return Math.abs(sum / 6.0);
    }

    private static double[] point(CSG csg, long index) {
        int i = (int) index;
        return new double[]{csg.getVertex_X(i), csg.getVertex_Y(i), csg.getVertex_Z(i)};
    }
}
