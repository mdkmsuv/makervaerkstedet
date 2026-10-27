import com.commonwealthrobotics.kloster.DimensionResize;
import com.commonwealthrobotics.kloster.LearningProjectService;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Group;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.MoveCenter;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.UnGroup;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Native save/reopen/history audit; no Stage, input dispatch, or custom operation. */
public final class ManipulationPersistenceProbe {
    private static final double EPS = 1e-4;
    private ManipulationPersistenceProbe() { }

    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("cadoodle-manipulation-persistence-");
        Path path = root.resolve("edited.doodle");
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        CaDoodleFile project = LearningProjectService.create(path.toFile(), "name-tag", "Persistence");
        try {
            List<CSG> parts = project.getCurrentState().stream().filter(c -> !c.isHole()).toList();
            require(parts.size() >= 2, "starter fixture needs two solid parts");
            String moved = parts.get(0).getName();
            String grouped = parts.get(1).getName();
            Snapshot starter = snapshot(project);

            join(project.addOperation(new MoveCenter().setNames(List.of(moved), project)
                    .setLocation(new TransformNR(13, -7, 4))));
            CSG movedShape = get(project, moved);
            double resizedX = movedShape.getTotalX() + 5;
            join(project.addOperation(DimensionResize.create(List.of(movedShape), new TransformNR(), 0,
                    resizedX, .5, .5, false)));
            Snapshot edited = snapshot(project);
            require(!same(starter, edited), "move/resize produced no observable edit");
            require(sameExcept(starter, edited, moved), "move/resize changed an unrelated part");
            require(edited.shapes.containsKey(grouped), "group candidate disappeared during move/resize");

            Group group = new Group().setNames(List.of(moved, grouped));
            group.setHull(false).setIntersect(false);
            join(project.addOperation(group));
            Snapshot groupedState = snapshot(project);
            require(groupedState.hasGroupResult(), "native group result missing before save");
            String groupName = groupedState.groupResultName();
            join(project.addOperation(new UnGroup().setNames(List.of(groupName))));
            Snapshot finalState = snapshot(project);
            require(!finalState.hasGroupResult(), "native ungroup left group result before save");
            require(!get(project, moved).isInGroup() && !get(project, grouped).isInGroup(),
                    "native ungroup left stale membership before save");

            project.save(true);
            CaDoodleFile reopened = CaDoodleFile.fromFile(path.toFile());
            try {
                assertSame(finalState, snapshot(reopened), "reopen");
                require(reopened.isBackAvailable(), "reopened project lost undo history");
                reopened.back();
                assertSame(groupedState, snapshot(reopened), "reopen undo to group");
                reopened.back();
                assertSame(edited, snapshot(reopened), "reopen undo to edited parts");
                require(reopened.isForwardAvailable(), "reopened project lost redo history");
                reopened.forward();
                assertSame(groupedState, snapshot(reopened), "reopen redo group");
                reopened.forward();
                assertSame(finalState, snapshot(reopened), "reopen redo");
                System.out.println("PASS: native move/resize/group/ungroup save-reopen and rebuilt undo/redo");
            } finally {
                reopened.close();
            }
        } finally {
            project.close();
        }
    }

    private static CSG get(CaDoodleFile project, String name) throws Exception {
        return CaDoodleFile.getByName(project.getCurrentState(), name);
    }

    private static void join(Thread thread) throws Exception {
        if (thread != null) {
            thread.join(30_000);
            require(!thread.isAlive(), "native operation exceeded 30 seconds");
        }
    }

    private static Snapshot snapshot(CaDoodleFile project) {
        Snapshot result = new Snapshot();
        for (CSG shape : project.getCurrentState()) result.shapes.put(shape.getName(), new State(shape));
        return result;
    }

    private static boolean same(Snapshot a, Snapshot b) {
        try { assertSame(a, b, "comparison"); return true; }
        catch (AssertionError mismatch) { return false; }
    }

    private static boolean sameExcept(Snapshot a, Snapshot b, String allowedName) {
        for (String name : a.shapes.keySet()) if (!name.equals(allowedName)) {
            State left = a.shapes.get(name), right = b.shapes.get(name);
            if (right == null || !left.same(right)) return false;
        }
        return true;
    }

    private static void assertSame(Snapshot expected, Snapshot actual, String step) {
        require(expected.shapes.keySet().equals(actual.shapes.keySet()), step + ": names/count changed");
        for (String name : expected.shapes.keySet())
            require(expected.shapes.get(name).same(actual.shapes.get(name)), step + ": changed " + name);
    }

    private static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }

    private static final class Snapshot {
        final Map<String, State> shapes = new LinkedHashMap<>();
        boolean hasGroupResult() { return shapes.values().stream().anyMatch(s -> s.groupResult); }
        String groupResultName() { return shapes.entrySet().stream().filter(e -> e.getValue().groupResult)
                .map(Map.Entry::getKey).findFirst().orElseThrow(); }
    }

    private static final class State {
        final double x, y, z, sx, sy, sz;
        final boolean hole, groupResult, inGroup;
        State(CSG c) { x=c.getCenterX(); y=c.getCenterY(); z=c.getCenterZ(); sx=c.getTotalX(); sy=c.getTotalY(); sz=c.getTotalZ();
            hole=c.isHole(); groupResult=c.isGroupResult(); inGroup=c.isInGroup(); }
        boolean same(State o) { return o != null && close(x,o.x)&&close(y,o.y)&&close(z,o.z)&&close(sx,o.sx)&&close(sy,o.sy)&&close(sz,o.sz)
                && hole==o.hole && groupResult==o.groupResult && inGroup==o.inGroup; }
    }
    private static boolean close(double a, double b) { return Math.abs(a-b) < EPS; }
}
