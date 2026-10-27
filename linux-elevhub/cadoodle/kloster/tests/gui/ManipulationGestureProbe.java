import com.neuronrobotics.bowlerkernel.Bezier3d.Manipulation;
import com.neuronrobotics.bowlerkernel.Bezier3d.IInteractiveUIElementProvider;
import com.neuronrobotics.sdk.addons.kinematics.math.*;
import eu.mihosoft.vrl.v3d.Vector3d;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.event.EventType;
import javafx.geometry.Point3D;
import javafx.scene.input.*;
import javafx.scene.shape.Rectangle;
import javafx.scene.transform.Affine;
import java.util.ArrayList;
import java.util.concurrent.*;

/** Actual native mouse path, with no Stage, user input or desktop access. */
public final class ManipulationGestureProbe {
    private static Affine delayedPreview;
    public static void main(String[] args) throws Exception {
        var finished = new CountDownLatch(1);
        Throwable[] failure = {null};
        boolean legacy = args.length > 0 && args[0].equals("--legacy");
        Platform.startup(() -> {
            try { run(legacy); } catch (Throwable error) { failure[0] = error; }
            finally { Platform.runLater(() -> {
                try { if (delayedPreview != null) near(delayedPreview.getTx(), 0, "queued drag survived abort"); }
                catch (Throwable error) { failure[0] = error; }
                finally { finished.countDown(); }
            }); }
        });
        if (!finished.await(30, TimeUnit.SECONDS)) throw new AssertionError("gesture probe timeout");
        Platform.exit();
        if (failure[0] != null) throw new AssertionError("native gesture regression", failure[0]);
        System.out.println("PASS: fast/repeated native drags, click jitter, synchronous snapshots, rotated frame, abort and invalid Z");
    }

    private static void run(boolean legacy) {
        Rectangle node = new Rectangle(200, 200);
        Affine preview = new Affine();
        var manipulation = new Manipulation(preview, new Vector3d(1, 1, 0), new TransformNR(),
                (x,y,grid) -> new Point3D(x,y,0), false, false);
        manipulation.setStartingWorkplanePosition(Point3D.ZERO);
        manipulation.setIncrement(1);
        node.addEventFilter(MouseEvent.ANY, manipulation.getMouseEvents());
        int[] handlePresses = {0};
        // ResizeSessionManager uses a bubbling handler to capture originalBounds
        // and beingUpdated after ResizingHandle's manipulation filter.
        node.setOnMousePressed(event -> handlePresses[0]++);
        press(node, 0, 0);
        require(handlePresses[0] == 1, "manipulation filter must allow corner press initialization");
        drag(node, 12, -8);
        near(manipulation.getCurrentPose().getX(), 12, "fast drag X");
        near(preview.getTy(), -8, "preview must be applied before release");
        if (legacy) return; // Earlier versions fail the actual fast-drag assertion above.
        var snapshots = new ArrayList<TransformNR>();
        manipulation.addSaveSnapshotListener(() -> {
            require(Platform.isFxApplicationThread(), "save snapshot must run on FX");
            snapshots.add(manipulation.getGlobalPoseInReferenceFrame().copy());
            manipulation.abortDrag();
        });
        release(node, 12, -8);
        require(snapshots.size() == 1, "exactly one snapshot per release");
        near(snapshots.getFirst().getX(), 12, "saved final drag X");
        near(snapshots.getFirst().getY(), -8, "saved final drag Y");
        for (int i = 1; i <= 40; i++) {
            manipulation.setStartingWorkplanePosition(Point3D.ZERO);
            press(node, 0, 0); drag(node, i+3, 5); release(node, i+3, 5);
            require(snapshots.size() == i+1, "one snapshot on repeated gesture " + i);
            near(snapshots.getLast().getX(), i+3, "no previous gesture offset");
        }
        int count = snapshots.size();
        press(node, 0, 0); drag(node, 1, 1); release(node, 1, 1);
        require(snapshots.size() == count, "click jitter must not save");
        press(node, 0, 0); drag(node, 20, 30);
        manipulation.abortDrag(); release(node, 20, 30);
        require(snapshots.size() == count, "pinch abort must not save drag");
        near(preview.getTx(), 0, "abort restores preview");
        require(!manipulation.isMoving(), "abort clears gesture state");
        // Touch drivers commonly emit a synthesized press/drag stream and an
        // ordinary (non-synthesized) release. The aborted stream must not
        // leak a save, and the next touch stream must still commit normally.
        press(node, 0, 0);
        drag(node, 18, 4);
        manipulation.abortDrag();
        release(node, 18, 4);
        require(snapshots.size() == count, "aborted synthesized touch stream must not save");
        press(node, 0, 0);
        drag(node, 9, 6);
        release(node, 9, 6);
        require(snapshots.size() == count + 1, "subsequent synthesized touch stream must save once");
        near(snapshots.getLast().getX(), 9, "subsequent touch drag X");
        near(snapshots.getLast().getY(), 6, "subsequent touch drag Y");
        manipulation.setFrameOfReference(() -> new TransformNR(100, 70, 20, RotationNR.getRotationZ(90)));
        manipulation.setStartingWorkplanePosition(Point3D.ZERO);
        press(node, 0, 0); drag(node, 10, 0); release(node, 10, 0);
        near(snapshots.getLast().getX(), 0, "rotated workplane world X");
        near(snapshots.getLast().getY(), 10, "rotated workplane world Y");
        near(snapshots.getFirst().getX(), 12, "later gestures must not mutate earlier snapshots");

        Rectangle zNode = new Rectangle(100,100);
        var z = new Manipulation(new Affine(), new Vector3d(0,0,1), new TransformNR(),
                (x,y,grid) -> new Point3D(0,0,Double.NaN), true, true);
        z.setStartingWorkplanePosition(Point3D.ZERO);
        zNode.addEventFilter(MouseEvent.ANY,z.getMouseEvents());
        int[] invalidSave = {0}; z.addSaveSnapshotListener(() -> invalidSave[0]++);
        press(zNode,0,0); drag(zNode,0,20); release(zNode,0,20);
        require(invalidSave[0] == 0 && Double.isFinite(z.getCurrentPose().getZ()), "invalid Z must not enter model");
        Manipulation.setUi(new IInteractiveUIElementProvider() {
            public javafx.scene.PerspectiveCamera getCamera() { return null; }
            public TransformNR getCamerFrame() { return new TransformNR(); }
            public double getCamerDepth() { return -1600; }
        });
        Rectangle fallbackNode = new Rectangle(200, 200);
        var fallback = new Manipulation(new Affine(), new Vector3d(1,1,0), new TransformNR());
        fallbackNode.addEventFilter(MouseEvent.ANY, fallback.getMouseEvents());
        press(fallbackNode,100,100); drag(fallbackNode,110,105); drag(fallbackNode,125,110);
        near(fallback.getCurrentPose().getX(), -25, "fallback drag must retain press origin");
        near(fallback.getCurrentPose().getY(), -10, "fallback drag Y origin");
        fallback.abortDrag();

        delayedPreview = new Affine();
        Rectangle delayedNode = new Rectangle(100, 100);
        var delayed = new Manipulation(delayedPreview, new Vector3d(1, 1, 0), new TransformNR(),
                (x,y,grid) -> new Point3D(x,y,0), false, false);
        delayed.setStartingWorkplanePosition(Point3D.ZERO);
        delayedNode.addEventFilter(MouseEvent.ANY, delayed.getMouseEvents());
        press(delayedNode, 0, 0);
        // Hold the FX queue while a non-FX provider submits previews. Abort
        // before those callbacks run; none may restore the discarded transform.
        Thread provider = new Thread(() -> {
            for (int i = 0; i < 20; i++) drag(delayedNode, 10 + i, 5);
        });
        provider.start();
        try { provider.join(3000); }
        catch (InterruptedException error) { throw new AssertionError(error); }
        require(!provider.isAlive(), "preview provider blocked on FX");
        delayed.abortDrag();
    }

    private static void press(Rectangle n,double x,double y) { fire(n,MouseEvent.MOUSE_PRESSED,x,y,true); }
    private static void drag(Rectangle n,double x,double y) { fire(n,MouseEvent.MOUSE_DRAGGED,x,y,true); }
    private static void release(Rectangle n,double x,double y) { fire(n,MouseEvent.MOUSE_RELEASED,x,y,false); }
    private static void fire(Rectangle node, EventType<MouseEvent> type, double x,double y,boolean down) {
        Event.fireEvent(node,new MouseEvent(type,x,y,x,y,MouseButton.PRIMARY,1,
                false,false,false,false,down,false,false,false,false,true,new PickResult(node,x,y)));
    }
    private static void near(double value,double expected,String message) { require(Math.abs(value-expected)<1e-6,message+": "+value+" != "+expected); }
    private static void require(boolean valid,String message) { if(!valid)throw new AssertionError(message); }
}
