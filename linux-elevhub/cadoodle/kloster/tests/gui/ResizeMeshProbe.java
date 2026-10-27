import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import javafx.event.Event;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.MouseButton;
import javafx.scene.shape.Mesh;
import javafx.scene.shape.MeshView;
import java.util.concurrent.atomic.AtomicInteger;
import com.commonwealthrobotics.controls.ResizeSessionManager;
import com.commonwealthrobotics.controls.SelectionSession;
import java.util.HashMap;
import java.util.WeakHashMap;
import javafx.scene.transform.Affine;
import javafx.scene.transform.Transform;

/** No-Stage regression for the MeshView identity-preserving rebuild operation. */
public final class ResizeMeshProbe {
    private static final class ControlsStub extends com.commonwealthrobotics.controls.ControlSprites {
        ControlsStub() { super(null, null, null, null, null, null, null); }
        @Override public void setMode(com.commonwealthrobotics.controls.SpriteDisplayMode mode) { }
    }
    public static void main(String[] args) throws Exception {
        javafx.application.Platform.startup(() -> {});
        try {
            var task = new java.util.concurrent.FutureTask<Void>(() -> { run(); return null; });
            javafx.application.Platform.runLater(task);
            task.get(30, java.util.concurrent.TimeUnit.SECONDS);
        } finally { javafx.application.Platform.exit(); }
    }
    private static void run() throws Exception {
        CSG original = new Cube(10, 10, 10).toCSG();
        CSG scaled = new Cube(25, 10, 10).toCSG();
        MeshView display = original.newMesh();
        Mesh originalMesh = display.getMesh();
        display.getTransforms().add(new javafx.scene.transform.Translate(5, -6, 7));
        AtomicInteger pressed = new AtomicInteger();
        display.addEventFilter(MouseEvent.MOUSE_PRESSED, event -> pressed.incrementAndGet());

        // Bypass only UI construction; invoke the actual production rebuild method.
        // No application, project, engine, Stage or window is constructed.
        var unsafeField = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
        unsafeField.setAccessible(true);
        var allocator = (sun.misc.Unsafe) unsafeField.get(null);
        var session = (SelectionSession) allocator.allocateInstance(SelectionSession.class);
        var manager = (ResizeSessionManager) allocator.allocateInstance(ResizeSessionManager.class);
        var holder = session.new MeshHolder(display, original.newMesh(), original.getBounds());
        var meshes = new HashMap<CSG, SelectionSession.MeshHolder>();
        meshes.put(original, holder);
        var meshesField = ResizeSessionManager.class.getDeclaredField("meshes");
        meshesField.setAccessible(true);
        meshesField.set(manager, meshes);
        session.setSelected(new java.util.LinkedHashSet<>(java.util.List.of(original)));
        var selectionField = ResizeSessionManager.class.getDeclaredField("session");
        selectionField.setAccessible(true); selectionField.set(manager, session);
        var controlsField = ResizeSessionManager.class.getDeclaredField("controlSprites");
        controlsField.setAccessible(true); controlsField.set(manager, allocator.allocateInstance(ControlsStub.class));
        var previewBases = ResizeSessionManager.class.getDeclaredField("previewBaseTransforms");
        previewBases.setAccessible(true);
        previewBases.set(manager, new WeakHashMap<MeshView, java.util.List<Transform>>());
        var preview = ResizeSessionManager.class.getDeclaredMethod("applyPreviewTransform", CSG.class, Affine.class);
        preview.setAccessible(true);
        int baseTransformCount = display.getTransforms().size();
        for (int i = 0; i < 100; i++) {
            Affine affine = new Affine();
            affine.appendScale(1.0 + i * 0.01, 1.0 + i * 0.005, 1.0 + i * 0.002);
            preview.invoke(manager, original, affine);
            require(holder.display == display, "preview replaced MeshView identity at sample " + i);
            require(display.getMesh() == originalMesh, "preview rebuilt geometry at sample " + i);
            require(display.getTransforms().size() == baseTransformCount + 1,
                    "preview accumulated transforms at sample " + i);
        }
        for (var wp : java.util.List.of(
                new com.neuronrobotics.sdk.addons.kinematics.math.TransformNR(),
                new com.neuronrobotics.sdk.addons.kinematics.math.TransformNR(20, -10, 7,
                    com.neuronrobotics.sdk.addons.kinematics.math.RotationNR.getRotationZ(45)),
                new com.neuronrobotics.sdk.addons.kinematics.math.TransformNR(20, 30, 40,
                    com.neuronrobotics.sdk.addons.kinematics.math.RotationNR.getRotationX(90)))) {
            Affine wpAffine = com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToAffine(wp);
            var scale = eu.mihosoft.vrl.v3d.Transform.unity().translate(3, -5, 2).scale(2, .5, 1.5);
            manager.rescaleMeshes(wpAffine, scale);
            var expected = original.transformed(com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToCSG(wp.inverse()))
                .transformed(scale).transformed(com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToCSG(wp))
                .movex(5).movey(-6).movez(7).getBounds();
            var actual = display.getBoundsInParent();
            near(actual.getMinX(), expected.getMinX()); near(actual.getMaxX(), expected.getMaxX());
            near(actual.getMinY(), expected.getMinY()); near(actual.getMaxY(), expected.getMaxY());
            near(actual.getMinZ(), expected.getMinZ()); near(actual.getMaxZ(), expected.getMaxZ());
            require(display.getMesh() == originalMesh, "workplane preview rebuilt geometry");
        }
        Event.fireEvent(display, new MouseEvent(display, display, MouseEvent.MOUSE_PRESSED,
                0, 0, 0, 0, MouseButton.NONE, 1,
                false, false, false, false, false, false, false, false, false, false,
                new javafx.scene.input.PickResult(display, 0, 0)));
        require(pressed.get() == 1, "preview lost registered MeshView event filter");
        var rebuild = ResizeSessionManager.class.getDeclaredMethod("regenerateMesh", CSG.class, CSG.class);
        rebuild.setAccessible(true);
        rebuild.invoke(manager, original, scaled);
        require(holder.display == display, "production rebuild replaced MeshView identity");

        require(display.getMesh() != originalMesh, "mesh geometry did not change");
        require(display.getBoundsInLocal().getWidth() > 10,
                "updated MeshView bounds were not exposed");
        Event.fireEvent(display, new MouseEvent(display, display, MouseEvent.MOUSE_PRESSED,
                0, 0, 0, 0, MouseButton.NONE, 1,
                false, false, false, false, false, false, false, false, false, false,
                new javafx.scene.input.PickResult(display, 0, 0)));
        require(pressed.get() == 2, "registered MeshView event filter was lost");
        rebuild.invoke(manager, original, original);
        require(Math.abs(display.getBoundsInLocal().getWidth() - 10) < 1e-6, "canonical geometry not restored");
        require(display.getTransforms().size() == baseTransformCount,
                "commit did not remove only the owned preview transform");
        rebuild.invoke(manager, scaled, scaled); // absent/replaced CSG is harmless
        System.out.println("PASS: actual resize manager retains MeshView identity, mouse filters, canonical geometry and missing-key safety");
    }
    private static void near(double actual, double expected) {
        require(Math.abs(actual - expected) < 1e-4, "preview differs from native resize: " + actual + " vs " + expected);
    }
    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
}
