import com.commonwealthrobotics.kloster.DimensionResize;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleOperation;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Delete;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Group;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Mirror;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.MirrorOrientation;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.MoveCenter;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Resize;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.UnGroup;
import com.neuronrobotics.sdk.addons.kinematics.math.RotationNR;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.Cylinder;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Native operation/timeline audit; no Stage, hit testing, or desktop input. */
public final class ManipulationHistoryProbe {
    private static final double EPS = 1e-4;
    private ManipulationHistoryProbe() { }

    public static void main(String[] args) throws Exception {
        // This probe audits native geometry and history; thumbnail rendering has
        // its own scene tests and must not touch a shared GUI from worker threads.
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        Path root = args.length == 1 ? Path.of(args[0]).toAbsolutePath()
                : Files.createTempDirectory("cadoodle-manipulation-history-");
        Files.createDirectories(root);
        CaDoodleFile project = new CaDoodleFile().setSelf(root.resolve("history.doodle").toFile())
                .setProjectName("Manipulation history probe");
        project.initialize();
        join(project.addOperation(new FixtureOperation()));

        List<Snapshot> history = new ArrayList<>();
        history.add(snapshot(project));

        join(project.addOperation(new MoveCenter().setNames(List.of("Base", "Cutter"), project)
                .setLocation(new TransformNR(17, -9, 0))));
        history.add(snapshot(project));
        expectShape(project, "Base", 30, -17, 5, 22, 16, 10, false);
        expectUnchangedOther(project, -30, 19, 4, 8, 8, 8);

        join(project.addOperation(new MoveCenter().setNames(List.of("Base"), project)
                .setLocation(new TransformNR(0, 0, 11))));
        history.add(snapshot(project));
        expectShape(project, "Base", 30, -17, 16, 22, 16, 10, false);
        expectUnchangedOther(project, -30, 19, 4, 8, 8, 8);

        CSG base = get(project, "Base");
        TransformNR pivot = new TransformNR(base.getCenterX(), base.getCenterY(), base.getCenterZ());
        TransformNR quarterTurn = pivot.times(new TransformNR(RotationNR.getRotationZ(90)))
                .times(pivot.inverse());
        join(project.addOperation(new MoveCenter().setNames(List.of("Base"), project)
                .setLocation(quarterTurn)));
        history.add(snapshot(project));
        expectShape(project, "Base", 30, -17, 16, 16, 22, 10, false);
        expectUnchangedOther(project, -30, 19, 4, 8, 8, 8);

        Resize resize = DimensionResize.create(List.of(get(project, "Base")), new TransformNR(), 0, 27,
                .5, .5, false);
        join(project.addOperation(resize));
        history.add(snapshot(project));
        expectShape(project, "Base", 30, -17, 16, 27, 22, 10, false);
        expectUnchangedOther(project, -30, 19, 4, 8, 8, 8);

        double[] beforeMirrorVertices = get(project, "Base").getVertices().clone();
        join(project.addOperation(new Mirror().setNames(List.of("Base", "Cutter"))
                .setLocation(MirrorOrientation.x).setWorkplane(new TransformNR())));
        history.add(snapshot(project));
        expectShape(project, "Base", 30, -17, 16, 27, 22, 10, false);
        require(verticesDiffer(beforeMirrorVertices, get(project, "Base").getVertices()),
                "mirror left asymmetric base vertices unchanged");
        expectUnchangedOther(project, -30, 19, 4, 8, 8, 8);

        Group group = new Group().setNames(List.of("Base", "Cutter"));
        group.setHull(false).setIntersect(false);
        double uncutVolume = volume(get(project, "Base"));
        join(project.addOperation(group));
        history.add(snapshot(project));
        require(snapshot(project).hasGroupResult(), "group operation produced no group result");
        CSG groupedBase = project.getCurrentState().stream().filter(CSG::isGroupResult).findFirst().orElseThrow();
        require(volume(groupedBase) > 0, "group with cutter produced empty geometry");
        require(volume(groupedBase) < uncutVolume - EPS, "hole did not remove material from base");
        require(get(project, "Cutter").isHole(), "group lost cutter hole metadata");
        require(get(project, "Base").isInGroup(), "group failed to hide its base member");

        String groupName = snapshot(project).groupResultName();
        join(project.addOperation(new UnGroup().setNames(List.of(groupName))));
        history.add(snapshot(project));
        require(!snapshot(project).hasGroupResult(), "ungroup left a group result");
        require(!get(project, "Base").isInGroup() && !get(project, "Cutter").isInGroup(),
                "ungroup failed to release its members");
        require(CaDoodleFile.getByName(project.getStateAtOperation(group), "Base").isInGroup(),
                "ungroup mutated the preceding group snapshot; undo would expose duplicate members");

        join(project.addOperation(new Delete().setNames(List.of("Other"))));
        history.add(snapshot(project));
        require(!snapshot(project).contains("Other"), "delete did not remove untouched object");

        for (int i = history.size() - 2; i >= 0; i--) {
            require(project.isBackAvailable(), "undo unavailable at step " + (i + 1));
            project.back();
            awaitSnapshot(history.get(i), project, "undo to history index " + i);
        }
        require(!project.isBackAvailable(), "undo did not reach fixture state");

        for (int i = 1; i < history.size(); i++) {
            require(project.isForwardAvailable(), "redo unavailable at step " + i);
            project.forward();
            awaitSnapshot(history.get(i), project, "redo step " + i);
        }
        require(!project.isForwardAvailable(), "redo left unapplied history");
        require(!snapshot(project).contains("Other"), "redo failed to preserve deleted-object history state");
        System.out.println("PASS: native moveXY/liftZ/rotate/resize/mirror/group/ungroup/delete with per-step undo/redo");
    }

    private static CSG get(CaDoodleFile project, String name) throws Exception {
        return CaDoodleFile.getByName(project.getCurrentState(), name);
    }

    private static Snapshot snapshot(CaDoodleFile project) {
        Snapshot result = new Snapshot();
        for (CSG shape : project.getCurrentState())
            result.shapes.put(shape.getName(), new ShapeState(shape));
        return result;
    }

    private static void expectShape(CaDoodleFile project, String name, double x, double y, double z,
                                    double sx, double sy, double sz, boolean hole) throws Exception {
        CSG shape = get(project, name);
        require(close(shape.getCenterX(), x) && close(shape.getCenterY(), y) && close(shape.getCenterZ(), z),
                name + ": explicit center expectation failed (actual " + shape.getCenterX() + ","
                        + shape.getCenterY() + "," + shape.getCenterZ() + ")");
        require(close(shape.getTotalX(), sx) && close(shape.getTotalY(), sy) && close(shape.getTotalZ(), sz),
                name + ": explicit bounds expectation failed (actual " + shape.getTotalX() + "x"
                        + shape.getTotalY() + "x" + shape.getTotalZ() + ")");
        require(shape.isHole() == hole, name + ": explicit hole expectation failed");
    }

    private static void expectUnchangedOther(CaDoodleFile project, double x, double y, double z,
                                             double sx, double sy, double sz) throws Exception {
        expectShape(project, "Other", x, y, z, sx, sy, sz, false);
    }

    private static void assertSnapshot(Snapshot expected, Snapshot actual, String step) {
        require(expected.shapes.keySet().equals(actual.shapes.keySet()), step + ": shape names/count differ");
        for (String name : expected.shapes.keySet())
            expected.shapes.get(name).assertSame(actual.shapes.get(name), step + " " + name);
    }

    private static void awaitSnapshot(Snapshot expected, CaDoodleFile project, String step)
            throws InterruptedException {
        AssertionError last = null;
        long deadline = System.nanoTime() + 30_000_000_000L;
        while (System.nanoTime() < deadline) {
            try {
                assertSnapshot(expected, snapshot(project), step);
                return;
            } catch (AssertionError mismatch) {
                last = mismatch;
                Thread.sleep(25);
            }
        }
        throw last == null ? new AssertionError(step + ": timeline did not settle") : last;
    }

    private static void join(Thread thread) throws InterruptedException {
        if (thread != null) {
            thread.join(30_000);
            require(!thread.isAlive(), "native operation exceeded 30 seconds");
        }
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static final class Snapshot {
        private final Map<String, ShapeState> shapes = new LinkedHashMap<>();
        boolean contains(String name) { return shapes.containsKey(name); }
        boolean hasGroupResult() { return shapes.values().stream().anyMatch(state -> state.groupResult); }
        String groupResultName() {
            return shapes.entrySet().stream().filter(entry -> entry.getValue().groupResult)
                    .map(Map.Entry::getKey).findFirst().orElseThrow();
        }
    }

    private static final class ShapeState {
        private final double x, y, z, sx, sy, sz;
        private final boolean hole, groupResult, inGroup;
        ShapeState(CSG shape) {
            x = shape.getCenterX(); y = shape.getCenterY(); z = shape.getCenterZ();
            sx = shape.getTotalX(); sy = shape.getTotalY(); sz = shape.getTotalZ();
            hole = shape.isHole(); groupResult = shape.isGroupResult(); inGroup = shape.isInGroup();
        }
        void assertSame(ShapeState other, String label) {
            require(other != null, label + ": missing shape");
            require(close(x, other.x) && close(y, other.y) && close(z, other.z), label + ": center changed");
            require(close(sx, other.sx) && close(sy, other.sy) && close(sz, other.sz), label + ": bounds changed");
            require(hole == other.hole && groupResult == other.groupResult && inGroup == other.inGroup,
                    label + ": hole/group metadata changed (expected hole=" + hole + ",group="
                            + groupResult + ",inGroup=" + inGroup + "; actual hole=" + other.hole
                            + ",group=" + other.groupResult + ",inGroup=" + other.inGroup + ")");
        }
    }

    private static boolean close(double a, double b) { return Math.abs(a - b) < EPS; }

    private static boolean verticesDiffer(double[] before, double[] after) {
        if (before.length != after.length) return true;
        for (int i = 0; i < before.length; i++)
            if (!close(before[i], after[i])) return true;
        return false;
    }

    private static double volume(CSG shape) {
        double[] vertices = shape.getVertices();
        long[] triangles = shape.getTriangles();
        double sum = 0;
        for (int i = 0; i < triangles.length; i += 3) {
            int a = (int) triangles[i] * 3, b = (int) triangles[i + 1] * 3, c = (int) triangles[i + 2] * 3;
            sum += vertices[a] * (vertices[b + 1] * vertices[c + 2] - vertices[b + 2] * vertices[c + 1])
                    - vertices[a + 1] * (vertices[b] * vertices[c + 2] - vertices[b + 2] * vertices[c])
                    + vertices[a + 2] * (vertices[b] * vertices[c + 1] - vertices[b + 1] * vertices[c]);
        }
        return Math.abs(sum / 6.0);
    }

    private static final class FixtureOperation extends CaDoodleOperation {
        @Override public String getType() { return "ManipulationHistoryFixture"; }
        @Override public List<CSG> process(List<CSG> previous) {
            ArrayList<CSG> state = new ArrayList<>(previous);
            state.add(new Cube(20, 16, 10).toCSG().union(new Cube(4, 4, 4).toCSG().movex(10))
                    .movex(12).movey(-8).movez(5).setName("Base"));
            state.add(new Cube(8, 8, 8).toCSG().movex(-30).movey(19).movez(4).setName("Other"));
            state.add(new Cylinder(4, 12).toCSG().movex(12).movey(-8).movez(4)
                    .setName("Cutter").setIsHole(true));
            return state;
        }
        @Override public List<String> getNamesAddedInThisOperation() {
            return List.of("Base", "Other", "Cutter");
        }
    }
}
