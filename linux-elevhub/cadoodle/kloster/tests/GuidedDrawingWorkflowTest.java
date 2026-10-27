import com.commonwealthrobotics.kloster.DimensionResize;
import com.commonwealthrobotics.kloster.LearningProjectService;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Group;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.MoveCenter;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Paste;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.ToHole;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.UnGroup;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Native operation audit for the guided two-hole pencil-holder course. */
public final class GuidedDrawingWorkflowTest {
    private static final double EPS = 1e-3;

    private GuidedDrawingWorkflowTest() { }

    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("cadoodle-guided-pencil-");
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        Path path = root.resolve("PencilHolder.doodle");
        CaDoodleFile project = LearningProjectService.create(path.toFile(), "pencil-holder", "Test");
        CSG outer = byName(project, "Blyantholder massiv blok");
        CSG cutter = byName(project, "Blyantholder skæreblok");
        String outerName = outer.getName();
        String cutterName = cutter.getName();
        require(!outer.isHole() && !cutter.isHole(), "starter parts must be solid");

        resize(project, outerName, 2, 40);
        resize(project, cutterName, 2, 40);
        // starterParts lays the two parts apart; put the first cutter 3 mm from
        // the outer X/Y edges while retaining its 3 mm floor.
        join(project.addOperation(new MoveCenter().setNames(List.of(cutterName), project)
                .setLocation(new TransformNR(-82, 3, 3))));
        join(project.addOperation(new ToHole().setNames(List.of(cutterName))));
        require(byName(project, cutterName).isHole(), "left cutter did not become a hole");

        Paste copyOperation = new Paste().setNames(List.of(cutterName))
                .setLocation(new TransformNR(28.5, 0, 0));
        join(project.addOperation(copyOperation));
        CSG right = project.getCurrentState().stream()
                .filter(shape -> shape.isHole() && !shape.getName().equals(cutterName))
                .findFirst().orElseThrow(() -> new AssertionError("native copy did not create a second cutter"));
        require(close(right.getCenterX(), byName(project, cutterName).getCenterX() + 28.5),
                "copied cutter moved by the wrong X distance");

        Group groupOperation = new Group().setNames(List.of(outerName, cutterName, right.getName()));
        join(project.addOperation(groupOperation));
        CSG result = project.getCurrentState().stream().filter(CSG::isGroupResult).findFirst()
                .orElseThrow(() -> new AssertionError("three-part pencil holder did not group"));
        assertFinishedHolder(result);

        project.save(true);
        CaDoodleFile reopened = CaDoodleFile.fromFile(path.toFile());
        join(reopened.regenerateAll());
        CSG restored = reopened.getCurrentState().stream().filter(CSG::isGroupResult).findFirst()
                .orElseThrow(() -> new AssertionError("grouped holder did not survive reopen"));
        assertFinishedHolder(restored);

        join(reopened.addOperation(new UnGroup().setNames(List.of(restored.getName()))));
        require(reopened.getCurrentState().stream().noneMatch(CSG::isGroupResult), "ungroup left a group result");
        require(reopened.isBackAvailable(), "ungroup was not recorded in undo history");
        reopened.back();
        require(reopened.getCurrentState().stream().anyMatch(CSG::isGroupResult), "undo did not restore group");
        require(reopened.isForwardAvailable(), "ungroup redo is unavailable");
        reopened.forward();
        require(reopened.getCurrentState().stream().noneMatch(CSG::isGroupResult), "redo did not ungroup");
        reopened.close();
        project.close();
        System.out.println("PASS: guided pencil-holder resize/place/hole/copy/group/ungroup/save/reopen/undo/redo");
    }

    private static void resize(CaDoodleFile project, String name, int axis, double target) throws Exception {
        CSG shape = byName(project, name);
        var operation = DimensionResize.create(List.of(shape), new TransformNR(), axis, target,
                .5, .5, false);
        require(operation != null, "resize operation was skipped for " + name);
        join(project.addOperation(operation));
        require(Math.abs(byName(project, name).getTotalZ() - target) < EPS,
                "resize did not set height for " + name);
    }

    private static void assertFinishedHolder(CSG result) {
        require(close(result.getTotalX(), 60) && close(result.getTotalY(), 35)
                && close(result.getTotalZ(), 40), "final holder bounds are wrong");
        require(close(result.getMinZ(), 0) && close(result.getMaxZ(), 40), "holder is not on z=0..40");
        double expected = 60 * 35 * 40 - 2 * 25.5 * 29 * 37;
        require(Math.abs(volume(result) - expected) < 1.0,
                "final holder volume is wrong: " + volume(result) + " expected=" + expected);
        // Mesh samples at z=3 demonstrate the two cavity floors; samples at
        // z=40 demonstrate that the cutters pass through the top.
        require(hasVertexAt(result, 3, 8, 33.5) && hasVertexAt(result, 3, 36.5, 62),
                "cavity floors/walls are missing at the 3 mm floor");
        require(hasVertexAt(result, 40, 8, 62), "cavity walls do not reach the top");
    }

    private static boolean hasVertexAt(CSG shape, double z, double minX, double maxX) {
        double[] vertices = shape.getVertices();
        for (int i = 0; i < vertices.length; i += 3)
            if (Math.abs(vertices[i + 2] - z) < .01
                    && vertices[i] >= minX - .01 && vertices[i] <= maxX + .01)
                return true;
        return false;
    }

    private static CSG byName(CaDoodleFile project, String name) throws Exception {
        CSG result = project.getCurrentState().stream()
                .filter(shape -> name.equals(shape.getName()) || name.equals(shape.getUserDefinedName()))
                .findFirst().orElse(null);
        if (result == null) throw new AssertionError("missing part: " + name);
        return result;
    }

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

    private static boolean close(double actual, double expected) {
        return Math.abs(actual - expected) < EPS;
    }

    private static void require(boolean ok, String message) {
        if (!ok) throw new AssertionError(message);
    }

    private static void join(Thread thread) throws InterruptedException {
        if (thread != null) thread.join();
    }
}
