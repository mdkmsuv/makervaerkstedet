import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine;
import javafx.application.ConditionalFeature;
import javafx.application.Platform;
import javafx.scene.*;
import javafx.scene.image.WritableImage;
import javafx.scene.paint.*;
import javafx.scene.shape.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Real 3D pixel regression. No Stage, desktop capture, input or image files. */
public final class WorkplaneTransparencyProbe {
    private static final Color PLANE = Color.web("#eff7fb");

    public static void main(String[] args) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        Throwable[] failure = new Throwable[1];
        Platform.startup(() -> {
            try {
                require(Platform.isSupported(ConditionalFeature.SCENE3D),
                        "A working 3D pipeline is required; software 2D cannot verify this regression");
                BowlerStudio3dEngine.setLightGrid(PLANE);
                for (Color object : new Color[]{Color.RED, Color.web("#009fc7")}) {
                    for (double alpha : new double[]{.2, 0, 1}) {
                        for (boolean rebuild : new boolean[]{false, true}) {
                            var grid = BowlerStudio3dEngine.createTexturedWorkplane(180, 180);
                            grid.setBackgroundOpacity(alpha);
                            if (rebuild) BowlerStudio3dEngine.makeGrid(grid);
                            Color expected = blend(PLANE, object, alpha);
                            check(render(grid, object, 15), expected, "model behind plane, alpha=" + alpha);
                            check(render(grid, object, -15), object, "model in front must retain its colour");
                        }
                    }
                }
                System.out.println("PASS: rendered pixels preserve red/blue models through 20% workplane, "
                        + "0/100% controls, depth ordering and grid rebuild; no window created");
            } catch (Throwable error) { failure[0] = error; }
            finally { done.countDown(); }
        });
        if (!done.await(60, TimeUnit.SECONDS)) throw new IllegalStateException("3D pixel probe timed out");
        Platform.exit();
        if (failure[0] != null) {
            failure[0].printStackTrace();
            System.exit(1);
        }
    }

    private static Color render(BowlerStudio3dEngine.GridHolder grid, Color colour, double z) {
        MeshView plane = grid.intersectionNode;
        // The real engine draws models before transparent workplane backgrounds.
        if (plane.getParent() instanceof Group previous) previous.getChildren().remove(plane);
        plane.setTranslateX(120); plane.setTranslateY(120);
        Box object = new Box(100, 100, 5);
        object.setTranslateX(120); object.setTranslateY(120); object.setTranslateZ(z);
        PhongMaterial material = new PhongMaterial(Color.BLACK);
        WritableImage ink = new WritableImage(1, 1);
        ink.getPixelWriter().setColor(0, 0, colour);
        material.setSelfIlluminationMap(ink);
        object.setMaterial(material);
        Group root = new Group(object, plane);
        Scene scene = new Scene(root, 240, 240, true, SceneAntialiasing.BALANCED);
        scene.setFill(Color.WHITE);
        scene.setCamera(new ParallelCamera());
        return scene.snapshot(null).getPixelReader().getColor(120, 120);
    }

    private static Color blend(Color foreground, Color background, double alpha) {
        return Color.color(foreground.getRed() * alpha + background.getRed() * (1 - alpha),
                foreground.getGreen() * alpha + background.getGreen() * (1 - alpha),
                foreground.getBlue() * alpha + background.getBlue() * (1 - alpha));
    }

    private static void check(Color actual, Color expected, String message) {
        double tolerance = 3.0 / 255;
        require(Math.abs(actual.getRed() - expected.getRed()) < tolerance
                        && Math.abs(actual.getGreen() - expected.getGreen()) < tolerance
                        && Math.abs(actual.getBlue() - expected.getBlue()) < tolerance,
                message + ": expected " + expected + ", rendered " + actual);
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
