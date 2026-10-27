import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Resize;
import com.commonwealthrobotics.kloster.DimensionResize;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.sdk.addons.kinematics.math.RotationNR;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.Bounds;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import java.util.List;

public final class DimensionResizeTest {
    private static final double EPS = 1e-5;

    public static void main(String[] args) {
        CSG object = cube("main", 31, 20, 20, 21.5, -64, 10);
        checkAxis(object, 0, 50, 0, 0, 50, 20, 20);
        checkAxis(object, 0, 30, 1, 1, 30, 20, 20);
        checkAxis(object, 1, 40, .5, .5, 31, 40, 20);
        checkAxis(object, 2, 35, .5, .5, 31, 20, 35);
        checkSequentialResizes(object);
        checkMixedAxes(object);
        checkAnchorEdges(object);
        checkUniform(object);
        checkAnchors(object);
        checkTransformedWorkplane();
        checkTiltedWorkplane();
        checkMultiSelection();
        checkInvalid(object);
        System.out.println("PASS: typed native dimension resize geometry, anchors, workplanes and validation");
    }

    private static void checkAxis(CSG object, int axis, double target, double ax, double ay,
            double sx, double sy, double sz) {
        Resize op = DimensionResize.create(List.of(object), new TransformNR(), axis, target, ax, ay, false);
        Bounds result = process(op, List.of(object)).get(0).getBounds();
        requireClose(result.getTotalX(), sx); requireClose(result.getTotalY(), sy); requireClose(result.getTotalZ(), sz);
    }

    private static void checkUniform(CSG object) {
        Resize op = DimensionResize.create(List.of(object), new TransformNR(), 0, 62, .5, .5, true);
        Bounds result = process(op, List.of(object)).get(0).getBounds();
        requireClose(result.getTotalX(), 62); requireClose(result.getTotalY(), 40); requireClose(result.getTotalZ(), 40);
        requireClose(result.getMinZ(), 0);
    }

    private static void checkSequentialResizes(CSG original) {
        CSG current = original;
        current = resized(current, 0, 50, .5, .5, false);
        requireClose(current.getBounds().getTotalX(), 50);
        current = resized(current, 0, 30, 0, .5, false);
        requireClose(current.getBounds().getTotalX(), 30);
        requireClose(current.getBounds().getMinX(), -3.5);
        current = resized(current, 0, 7.5, 1, .5, false);
        requireClose(current.getBounds().getTotalX(), 7.5);
        requireClose(current.getBounds().getMaxX(), 26.5);
        requireClose(current.getBounds().getMinZ(), 0);
    }

    private static void checkMixedAxes(CSG original) {
        CSG current = resized(original, 1, 40, .5, 0, false);
        current = resized(current, 2, 35, .5, .5, false);
        current = resized(current, 0, 15, .5, .5, false);
        Bounds bounds = current.getBounds();
        requireClose(bounds.getTotalX(), 15); requireClose(bounds.getTotalY(), 40); requireClose(bounds.getTotalZ(), 35);
        requireClose(bounds.getMinY(), -74); requireClose(bounds.getMinZ(), 0);
    }

    private static void checkAnchorEdges(CSG original) {
        for (double anchor : new double[] {0, .5, 1}) {
            Bounds source = original.getBounds();
            Bounds resultX = resized(original, 0, 40, anchor, .5, false).getBounds();
            double expectedMinX = source.getMinX() + (source.getTotalX() - 40) * anchor;
            requireClose(resultX.getMinX(), expectedMinX);
            requireClose(resultX.getTotalY(), source.getTotalY());
            requireClose(resultX.getTotalZ(), source.getTotalZ());

            Bounds resultY = resized(original, 1, 30, .5, anchor, false).getBounds();
            double expectedMinY = source.getMinY() + (source.getTotalY() - 30) * anchor;
            requireClose(resultY.getMinY(), expectedMinY);
            requireClose(resultY.getTotalX(), source.getTotalX());
            requireClose(resultY.getTotalZ(), source.getTotalZ());
        }
    }

    private static void checkAnchors(CSG object) {
        Resize op = DimensionResize.create(List.of(object), new TransformNR(), 0, 50, 0, 1, false);
        Bounds result = process(op, List.of(object)).get(0).getBounds();
        requireClose(result.getMinX(), 6); requireClose(result.getMaxX(), 56);
        requireClose(result.getMaxY(), -54); requireClose(result.getMinZ(), 0);
    }

    private static void checkTransformedWorkplane() {
        CSG object = cube("rotated", 10, 20, 30, 10, 20, 40);
        TransformNR wp = new TransformNR(100, -50, 7, RotationNR.getRotationZ(90));
        Resize op = DimensionResize.create(List.of(object), wp, 1, 40, .5, .5, false);
        Bounds local = process(op, List.of(object)).get(0).transformed(
                com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToCSG(wp.inverse())).getBounds();
        requireClose(local.getTotalX(), 20); requireClose(local.getTotalY(), 40); requireClose(local.getTotalZ(), 30);
        requireClose(local.getMinZ(), 18);
    }

    private static void checkTiltedWorkplane() {
        TransformNR wp = new TransformNR(20, 30, 40, RotationNR.getRotationX(90));
        CSG localObject = cube("tilted", 12, 18, 24, 5, -10, 3);
        CSG worldObject = localObject.transformed(
                com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToCSG(wp))
                .setName("tilted");
        Resize op = DimensionResize.create(List.of(worldObject), wp, 2, 36, .5, .5, false);
        Bounds local = process(op, List.of(worldObject)).get(0).transformed(
                com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToCSG(wp.inverse())).getBounds();
        requireClose(local.getTotalX(), 12); requireClose(local.getTotalY(), 18); requireClose(local.getTotalZ(), 36);
        requireClose(local.getMinZ(), -9);
    }

    private static void checkMultiSelection() {
        CSG a = cube("a", 10, 10, 10, 0, 0, 0);
        CSG b = cube("b", 10, 10, 10, 30, 0, 0);
        CSG untouched = cube("untouched", 8, 8, 8, 100, 0, 0);
        Resize op = DimensionResize.create(List.of(a, b), new TransformNR(), 0, 60, .5, .5, false);
        List<CSG> result = process(op, List.of(a, b, untouched));
        Bounds first = result.get(0).getBounds();
        Bounds second = result.get(1).getBounds();
        requireClose(Math.max(first.getMaxX(), second.getMaxX())
                - Math.min(first.getMinX(), second.getMinX()), 60);
        requireClose(first.getTotalX(), 15); requireClose(second.getTotalX(), 15);
        requireClose(result.get(2).getBounds().getTotalX(), 8);
    }

    private static void checkInvalid(CSG object) {
        require(DimensionResize.create(List.of(), new TransformNR(), 0, 10, .5, .5, false) == null);
        require(DimensionResize.create(List.of(object), new TransformNR(), 0, 31, .5, .5, false) == null);
        for (double value : new double[] {0, -1, Double.NaN, Double.POSITIVE_INFINITY})
            throwsIllegal(() -> DimensionResize.create(List.of(object), new TransformNR(), 0, value, .5, .5, false));
        throwsIllegal(() -> DimensionResize.create(List.of(object), new TransformNR(), 3, 10, .5, .5, false));
        throwsIllegal(() -> DimensionResize.create(List.of(object), new TransformNR(), 0, 10, -1, .5, false));
    }

    private static CSG cube(String name, double x, double y, double z, double tx, double ty, double tz) {
        return new Cube(x, y, z).toCSG().movex(tx).movey(ty).movez(tz).setName(name);
    }

    private static List<CSG> process(Resize op, List<CSG> input) {
        op.setCaDoodleFile(new CaDoodleFile());
        return op.process(input);
    }

    private static CSG resized(CSG object, int axis, double target, double anchorX,
            double anchorY, boolean uniform) {
        Resize op = DimensionResize.create(List.of(object), new TransformNR(), axis, target,
                anchorX, anchorY, uniform);
        return process(op, List.of(object)).get(0);
    }

    private static void requireClose(double actual, double expected) {
        require(Math.abs(actual - expected) < EPS, "expected " + expected + " got " + actual);
    }

    private static void throwsIllegal(Runnable action) {
        try { action.run(); throw new AssertionError("expected IllegalArgumentException"); }
        catch (IllegalArgumentException expected) { }
    }

    private static void require(boolean condition) {
        if (!condition) throw new AssertionError();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
