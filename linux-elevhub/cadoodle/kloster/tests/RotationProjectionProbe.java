import com.commonwealthrobotics.rotate.RotationHandle;
import javafx.application.Platform;
import javafx.geometry.Point2D;
import javafx.geometry.Point3D;
import javafx.scene.Scene;
import javafx.scene.image.ImageView;
import javafx.scene.input.MouseButton;
import javafx.scene.input.MouseEvent;
import javafx.scene.input.PickResult;
import javafx.scene.layout.Pane;
import javafx.scene.shape.Box;
import javafx.stage.Stage;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;

/** Real screen-to-ring projection, run only on a private Xvfb display. */
public class RotationProjectionProbe {
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> {});
        FutureTask<Void> test = new FutureTask<>(() -> {
            var unsafeField = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
            unsafeField.setAccessible(true);
            var handle = (RotationHandle)((sun.misc.Unsafe)unsafeField.get(null)).allocateInstance(RotationHandle.class);
            ImageView circle = new ImageView();
            circle.setFitWidth(100); circle.setFitHeight(100);
            circle.setTranslateX(60); circle.setTranslateY(70);
            var field = RotationHandle.class.getDeclaredField("controlCircle");
            field.setAccessible(true); field.set(handle, circle);
            var angle = RotationHandle.class.getDeclaredMethod("getAngle", MouseEvent.class);
            angle.setAccessible(true);
            Pane pane = new Pane(circle);
            Stage stage = new Stage(); stage.setScene(new Scene(pane, 320, 260)); stage.show();
            try {
                for (Point2D local : new Point2D[]{new Point2D(90,55), new Point2D(130,20)}) {
                    Point2D screen = circle.localToScreen(local);
                    double expected = local.getX() == 90 ? 0 : Math.toDegrees(Math.atan2(-30,80));
                    for (var node : new javafx.scene.Node[]{circle, new Box(10,10,500), null}) {
                        var event = new MouseEvent(MouseEvent.MOUSE_DRAGGED, 0,0,screen.getX(),screen.getY(),
                                MouseButton.PRIMARY,1,false,false,false,false,true,false,false,true,false,false,
                                new PickResult(node,new Point3D(3,7,250),1));
                        double actual = (double) angle.invoke(handle, event);
                        if (Math.abs(actual-expected)>1e-6)
                            throw new AssertionError("ring projection changed with picked geometry: " + actual + " != " + expected);
                    }
                }
            } finally { stage.hide(); }
            return null;
        });
        try {
            Platform.runLater(test); test.get(20,TimeUnit.SECONDS);
            System.out.println("PASS: inner snap and outer rotation remain stable across ring, model-depth and empty picks");
        } finally { Platform.exit(); }
    }
}
