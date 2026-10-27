import com.commonwealthrobotics.kloster.DimensionResize;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleOperation;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Delete;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Group;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.MoveCenter;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Paste;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Resize;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.UnGroup;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import com.neuronrobotics.sdk.addons.kinematics.math.RotationNR;
import eu.mihosoft.vrl.v3d.Bounds;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import eu.mihosoft.vrl.v3d.Cylinder;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/** Headless ordinary-drawing workflow audit; no Stage or input dispatch. */
public final class DrawingWorkflowTest {
    private static final double EPS = 1e-4;
    private DrawingWorkflowTest() { }

    public static void main(String[] args) throws Exception {
        Path root = args.length == 1 ? Path.of(args[0]).toAbsolutePath()
                : Files.createTempDirectory("cadoodle-interaction-audit-");
        Files.createDirectories(root);
        Path privateHome = root.resolve("fixture-home");
        Files.createDirectories(privateHome);
        System.setProperty("user.home", privateHome.toString());
        // Rendering is covered separately. Keep timeline thumbnails away from
        // the real scene graph in this geometry/timeline test.
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));

        CaDoodleFile project = new CaDoodleFile().setSelf(root.resolve("workflow.doodle").toFile())
                .setProjectName("Headless interaction audit");
        project.initialize();
        Thread added = project.addOperation(new WorkflowOperation(false));
        if (added != null) added.join();
        Thread addedHole = project.addOperation(new WorkflowOperation(true));
        if (addedHole != null) addedHole.join();

        List<CSG> initial = project.getCurrentState();
        require(initial.size() == 2, "native operation did not create two shapes");
        CSG solid = CaDoodleFile.getByName(initial, "Solid");
        CSG hole = CaDoodleFile.getByName(initial, "Hole");
        require(hole.isHole(), "hole metadata was lost");
        require(close(solid.getTotalX(), 20) && close(solid.getTotalY(), 16)
                && close(solid.getTotalZ(), 10), "initial solid bounds changed");

        MoveCenter move = new MoveCenter().setNames(List.of("Solid", "Hole"), project)
                .setLocation(new TransformNR(31, -17, 9));
        join(project.addOperation(move));
        CSG moved = CaDoodleFile.getByName(project.getCurrentState(), "Solid");
        require(close(moved.getCenterX(), solid.getCenterX() + 31)
                && close(moved.getCenterY(), solid.getCenterY() - 17)
                && close(moved.getCenterZ(), solid.getCenterZ() + 9),
                "native off-origin move did not persist");

        Resize resize = DimensionResize.create(List.of(moved), new TransformNR(), 0, 24,
                .5, .5, false);
        join(project.addOperation(resize));
        CSG resized = CaDoodleFile.getByName(project.getCurrentState(), "Solid");
        require(close(resized.getTotalX(), 24) && close(resized.getTotalY(), 16),
                "native resize bounds are wrong");

        Paste paste = new Paste().setNames(List.of("Solid"))
                .setLocation(new TransformNR(5, 0, 0));
        join(project.addOperation(paste));
        require(project.getCurrentState().size() >= 3,
                "native paste did not duplicate the selected solid");
        CSG copy = project.getCurrentState().stream()
                .filter(shape -> !List.of("Solid", "Hole").contains(shape.getName()))
                .findFirst().orElseThrow();
        require(close(copy.getTotalX(), 24) && close(copy.getTotalY(), 16)
                && close(copy.getCenterX(), resized.getCenterX() + 5),
                "native paste changed dimensions or used the wrong offset");

        // The UI records rotations through MoveCenter with a rotation transform.
        TransformNR pivot = new TransformNR(copy.getCenterX(), copy.getCenterY(), copy.getCenterZ());
        TransformNR quarterTurn = pivot.times(new TransformNR(RotationNR.getRotationZ(90)))
                .times(pivot.inverse());
        join(project.addOperation(new MoveCenter().setNames(List.of(copy.getName()), project)
                .setLocation(quarterTurn)));
        CSG rotated = CaDoodleFile.getByName(project.getCurrentState(), copy.getName());
        require(close(rotated.getTotalX(), 16) && close(rotated.getTotalY(), 24)
                && close(rotated.getTotalZ(), 10), "native rotation did not rotate the box dimensions");
        require(close(rotated.getCenterX(), copy.getCenterX())
                && close(rotated.getCenterY(), copy.getCenterY()), "rotation moved the selected pivot");

        Group group = new Group().setNames(List.of("Solid", "Hole"));
        join(project.addOperation(group));
        List<CSG> grouped = project.getCurrentState();
        require(grouped.stream().anyMatch(CSG::isGroupResult), "native group did not create a result");
        require(grouped.stream().filter(shape -> shape.checkGroupMembership(group.getGroupID())).count() >= 2,
                "native group did not mark both constituents");
        CSG groupResult = grouped.stream().filter(CSG::isGroupResult).findFirst().orElseThrow();
        require(volume(groupResult) > 0 && volume(groupResult) < volume(resized) - 1,
                "native group did not subtract the overlapping hole");
        String groupResultName = groupResult.getName();
        join(project.addOperation(new UnGroup().setNames(List.of(groupResultName))));
        require(project.getCurrentState().stream().noneMatch(CSG::isGroupResult),
                "native ungroup left its result in state");
        require(!CaDoodleFile.getByName(project.getCurrentState(), "Solid").isInGroup()
                && CaDoodleFile.getByName(project.getCurrentState(), "Hole").isHole(),
                "native ungroup did not restore independent solid and hole");
        join(project.addOperation(new Delete().setNames(List.of("Hole"))));
        require(project.getCurrentState().stream().noneMatch(shape -> "Hole".equals(shape.getName())),
                "native delete did not remove the hole");

        try {
            project.toJson();
            System.out.println("PASS: native operation serialization");
        } catch (RuntimeException unsupported) {
            System.out.println("LIMIT: custom fixture operation is not registered for JSON: "
                    + unsupported.getClass().getSimpleName());
        }
        require(project.isBackAvailable(), "timeline cannot undo native delete");
        project.back();
        require(project.getCurrentState().stream().anyMatch(shape -> "Hole".equals(shape.getName())),
                "undo did not restore deleted shape");
        require(project.isForwardAvailable(), "timeline cannot redo native delete");
        project.forward();
        require(project.getCurrentState().stream().noneMatch(shape -> "Hole".equals(shape.getName())),
                "redo did not delete shape");
        Bounds finalBounds = project.getBounds(project.getCurrentState());
        require(finalBounds.getTotalX() > 0 && finalBounds.getTotalY() > 0 && finalBounds.getTotalZ() > 0,
                "replayed state has invalid aggregate bounds");
        System.out.println("PASS: native drawing workflow move/resize/paste/rotate/hole-group/ungroup/delete/undo/redo");
    }

    private static boolean close(double actual, double expected) {
        return Math.abs(actual - expected) < EPS;
    }

    // CSG.getVolume() is a cached manufacturing value; sum the actual triangle mesh.
    private static double volume(CSG shape) {
        double[] v = shape.getVertices();
        long[] t = shape.getTriangles();
        double sum = 0;
        for (int i = 0; i < t.length; i += 3) {
            int a = (int)t[i] * 3, b = (int)t[i + 1] * 3, c = (int)t[i + 2] * 3;
            sum += v[a] * (v[b + 1] * v[c + 2] - v[b + 2] * v[c + 1])
                    - v[a + 1] * (v[b] * v[c + 2] - v[b + 2] * v[c])
                    + v[a + 2] * (v[b] * v[c + 1] - v[b + 1] * v[c]);
        }
        return Math.abs(sum / 6.0);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    private static void join(Thread thread) throws InterruptedException {
        if (thread != null) thread.join();
    }

    private static final class WorkflowOperation extends CaDoodleOperation {
        private final boolean holeOnly;

        private WorkflowOperation(boolean holeOnly) {
            this.holeOnly = holeOnly;
        }

        @Override public String getType() { return "WorkflowAudit"; }

        @Override public List<CSG> process(List<CSG> previous) {
            ArrayList<CSG> state = new ArrayList<>(previous);
            if (!holeOnly) {
                state.add(new Cube(20, 16, 10).toCSG().movex(12).movey(-8).movez(5).setName("Solid"));
            } else {
                state.add(new Cylinder(4, 12).toCSG().movex(12).movey(-8).movez(4)
                        .setName("Hole").setIsHole(true));
            }
            return state;
        }

        @Override public List<String> getNamesAddedInThisOperation() {
            return holeOnly ? List.of("Hole") : List.of("Solid");
        }
    }
}
