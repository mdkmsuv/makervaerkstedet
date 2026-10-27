package com.commonwealthrobotics.kloster;

import com.neuronrobotics.bowlerstudio.physics.TransformFactory;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.Resize;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.Bounds;
import eu.mihosoft.vrl.v3d.CSG;
import java.util.ArrayList;
import java.util.List;

/** Builds the native CaDoodle Resize operation from typed dimensions. */
public final class DimensionResize {
    private static final double EPS = 1e-9;

    private DimensionResize() { }

    /**
     * Creates a native resize operation in workplane coordinates.
     * axis is X=0, Y=1, Z=2. Anchor fractions apply to changed X/Y edges.
     */
    public static Resize create(List<CSG> selected, TransformNR workplane, int axis,
            double target, double anchorX, double anchorY, boolean uniform) {
        if (selected == null || selected.isEmpty()) return null;
        if (workplane == null) throw new IllegalArgumentException("workplane is required");
        if (axis < 0 || axis > 2) throw new IllegalArgumentException("axis must be 0, 1 or 2");
        if (!finitePositive(target)) throw new IllegalArgumentException("target must be finite and positive");
        checkAnchor(anchorX, "anchorX");
        checkAnchor(anchorY, "anchorY");

        Bounds bounds = boundsInWorkplane(selected, workplane);
        double[] min = {bounds.getMinX(), bounds.getMinY(), bounds.getMinZ()};
        double[] max = {bounds.getMaxX(), bounds.getMaxY(), bounds.getMaxZ()};
        double[] oldSize = {max[0] - min[0], max[1] - min[1], max[2] - min[2]};
        for (int i = 0; i < 3; i++)
            if (!finitePositive(oldSize[i])) throw new IllegalArgumentException("selected bounds have zero extent");

        double[] newSize = oldSize.clone();
        if (uniform) {
            double scale = target / oldSize[axis];
            for (int i = 0; i < 3; i++) newSize[i] = oldSize[i] * scale;
        } else {
            newSize[axis] = target;
        }
        if (sameSizes(oldSize, newSize)) return null;

        double[] newMin = min.clone();
        double[] newMax = max.clone();
        // Z always grows from the existing bottom, even for uniform scaling.
        newMin[2] = min[2];
        for (int i = 0; i < 3; i++) {
            if (i == 2) {
                newMax[i] = newMin[i] + newSize[i];
            } else {
                double anchor = i == 0 ? anchorX : anchorY;
                newMin[i] = min[i] + (oldSize[i] - newSize[i]) * anchor;
                newMax[i] = newMin[i] + newSize[i];
            }
        }

        List<String> names = new ArrayList<>();
        for (CSG csg : selected) {
            if (csg == null || csg.getName() == null || csg.getName().isBlank())
                throw new IllegalArgumentException("selected CSG must have a name");
            names.add(csg.getName());
        }
        TransformNR operationWorkplane = new TransformNR(workplane);
        return new Resize().setNames(names).setWorkplane(operationWorkplane).setResize(
                new TransformNR((min[0] + max[0]) / 2, (min[1] + max[1]) / 2, newMax[2]),
                new TransformNR(newMax[0], newMax[1], min[2]),
                new TransformNR(newMin[0], newMin[1], min[2]));
    }

    private static Bounds boundsInWorkplane(List<CSG> selected, TransformNR workplane) {
        Bounds total = null;
        var inverse = TransformFactory.nrToCSG(new TransformNR(workplane).inverse());
        for (CSG csg : selected) {
            if (csg == null) throw new IllegalArgumentException("selected CSG cannot be null");
            Bounds local = csg.transformed(inverse).getBounds();
            total = total == null ? local : union(total, local);
        }
        if (total == null) return new Bounds(eu.mihosoft.vrl.v3d.Vector3d.ZERO,
                eu.mihosoft.vrl.v3d.Vector3d.ZERO);
        return total;
    }

    private static Bounds union(Bounds a, Bounds b) {
        return new Bounds(
                eu.mihosoft.vrl.v3d.Vector3d.xyz(Math.min(a.getMinX(), b.getMinX()),
                        Math.min(a.getMinY(), b.getMinY()), Math.min(a.getMinZ(), b.getMinZ())),
                eu.mihosoft.vrl.v3d.Vector3d.xyz(Math.max(a.getMaxX(), b.getMaxX()),
                        Math.max(a.getMaxY(), b.getMaxY()), Math.max(a.getMaxZ(), b.getMaxZ())));
    }

    private static boolean finitePositive(double value) {
        return Double.isFinite(value) && value > 0;
    }

    private static void checkAnchor(double value, String name) {
        if (!Double.isFinite(value) || value < 0 || value > 1)
            throw new IllegalArgumentException(name + " must be between 0 and 1");
    }

    private static boolean sameSizes(double[] oldSize, double[] newSize) {
        for (int i = 0; i < 3; i++)
            if (Math.abs(oldSize[i] - newSize[i]) > EPS) return false;
        return true;
    }
}
