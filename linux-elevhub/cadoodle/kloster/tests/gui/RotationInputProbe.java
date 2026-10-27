import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.*;
import com.commonwealthrobotics.rotate.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.sdk.addons.kinematics.math.*;
import eu.mihosoft.vrl.v3d.Cube;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.geometry.Point3D;
import javafx.scene.image.ImageView;
import javafx.scene.input.*;
import javafx.scene.transform.Affine;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicInteger;

/** Real rotation event handlers, no Stage; only app/session construction is stubbed. */
public final class RotationInputProbe {
    static class FileStub extends CaDoodleFile {
        @Override public TransformNR getWorkplane() { return new TransformNR(); }
    }
    static class ProjectStub extends ActiveProject {
        CaDoodleFile file;
        @Override public CaDoodleFile get() { return file; }
    }
    static class SessionStub extends SelectionSession {
        SessionStub() { super(null,null,null); }
        @Override public void setMode(SpriteDisplayMode mode) { }
        @Override public boolean moveLock() { return false; }
    }
    static class ManagerStub extends RotationSessionManager {
        ManagerStub() { super(null,null,null,null,null,null); }
        @Override public void initialize(boolean lock) { }
        @Override public void hideOthers(RotationHandle handle) { }
    }
    static RotationHandle handle;
    static ImageView circle;
    static Affine preview;
    static AtomicInteger commits = new AtomicInteger();
    static TransformNR committed;

    public static void main(String[] args) throws Exception {
        Platform.startup(() -> {});
        try {
            fx(() -> {
                var field=sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); field.setAccessible(true);
                var unsafe=(sun.misc.Unsafe)field.get(null);
                var project=(ProjectStub)unsafe.allocateInstance(ProjectStub.class); project.file=new FileStub();
                var session=(SessionStub)unsafe.allocateInstance(SessionStub.class);
                var manager=(ManagerStub)unsafe.allocateInstance(ManagerStub.class);
                preview=new Affine();
                handle=new RotationHandle(EulerAxis.azimuth,new Affine(),preview,manager,project,session,new Affine(),new com.commonwealthrobotics.RulerManager(null),
                    pose -> { commits.incrementAndGet(); committed=pose; });
                set(handle,"bounds",new Cube(20,10,5).toCSG().movex(12).movey(-8).movez(6).getBounds());
                var circleField=RotationHandle.class.getDeclaredField("controlCircle");circleField.setAccessible(true);
                circle=(ImageView)circleField.get(handle);circle.setFitWidth(100);circle.setFitHeight(100);
                var handleField=RotationHandle.class.getDeclaredField("handle");handleField.setAccessible(true);
                var handleImage=(ImageView)handleField.get(handle);
                // Preserve the established inner-ring 22.5 degree snap.
                var angleMethod=RotationHandle.class.getDeclaredMethod("getAngle",MouseEvent.class);
                angleMethod.setAccessible(true);
                var fineSample=new MouseEvent(MouseEvent.MOUSE_DRAGGED,90,55,90,55,MouseButton.PRIMARY,1,
                    false,false,false,false,true,false,false,true,false,false,
                    new PickResult(circle,new Point3D(90,55,0),1));
                double fineAngle=(double)angleMethod.invoke(handle,fineSample);
                require(Math.abs(fineAngle) < 1e-9, "inner rotation snap changed");
                // No pressed event: a driver tail cannot create a new rotation.
                dragQuarterTurn(); release(); require(commits.get()==0,"orphan drag/release committed");
                // After the first accepted sample, a driver may report the
                // ring child/overlay as the pick target. It must not pause the
                // active preview until the pointer re-enters the circle.
                press(); event(MouseEvent.MOUSE_DRAGGED,90,50,true);
                // The overlay has a different local origin: equivalent world
                // pointer coordinates must produce the same native rotation.
                var overlayPoint = handleImage.sceneToLocal(circle.localToScene(new Point3D(50,10,0)));
                event(handleImage,MouseEvent.MOUSE_DRAGGED,overlayPoint.getX(),overlayPoint.getY(),true);
                require(!preview.isIdentity(),"drag sample over handle was dropped");
                var expectedPreview = preview.transform(22,-8,6);
                near(expectedPreview.getX(),12,"overlay coordinates changed preview X");
                near(expectedPreview.getY(),-18,"overlay coordinates changed preview Y");
                handle.abortInteraction(); release(); require(commits.get()==0,"overlay sample committed");
                press(); dragQuarterTurn();
                require(!preview.isIdentity(),"rotation preview never applied");
                handle.abortInteraction(); release();
                require(commits.get()==0,"release after pinch cancellation saved a rotation");
                require(preview.isIdentity(),"cancel left rotated preview");
                press(); dragQuarterTurn(); release(); release();
                require(commits.get()==1,"one rotation release must commit exactly once");
                require(preview.isIdentity(),"commit left preview applied twice");
                require(committed!=null,"no native rotation captured");
                assertRotation(-90);
                handle.setLock(true); press(); dragQuarterTurn(); release();
                require(commits.get()==1,"locked rotation committed");
                handle.setLock(false);
                // Typing an angle then cancelling must not commit through hide().
                press();
                var number=(javafx.scene.control.TextField)handle.TDnumber.getTextField().getFirst();
                number.setText("45");handle.abortInteraction();
                require(commits.get()==1,"cancel committed a dirty numeric angle");
                press(); number.setText("22,5");
                number.fireEvent(new KeyEvent(KeyEvent.KEY_PRESSED,"","",KeyCode.ENTER,false,false,false,false));
                require(commits.get()==2,"comma angle did not commit exactly once");
                assertRotation(-22.5);
                press(); number.setText("45.5");
                number.fireEvent(new KeyEvent(KeyEvent.KEY_PRESSED,"","",KeyCode.ENTER,false,false,false,false));
                require(commits.get()==3,"dot angle did not commit exactly once");
                assertRotation(-45.5);
            });
            // Exercise a pending preview callback that was queued before cancel.
            fx(() -> { press(); });
            var sweep=RotationHandle.class.getDeclaredMethod("setSweepAngle",double.class); sweep.setAccessible(true);
            CountDownLatch blocked=new CountDownLatch(1), allow=new CountDownLatch(1);
            Platform.runLater(() -> {
                blocked.countDown();
                try { require(allow.await(5,TimeUnit.SECONDS),"blocked FX timeout"); handle.abortInteraction(); }
                catch (InterruptedException error) { throw new AssertionError(error); }
            });
            require(blocked.await(5,TimeUnit.SECONDS),"FX not blocked");
            try { sweep.invoke(handle,45.0); } finally { allow.countDown(); }
            fx(() -> {
                require(preview.isIdentity(),"queued pre-cancel preview resurrected");
                release(); require(commits.get()==3,"queued pre-cancel rotation committed");
            });
            System.out.println("PASS: real rotation press/drag/release and pivot geometry, comma/dot angles, single commit, lock, numeric cancel and stale preview cancellation");
        } finally { Platform.exit(); }
    }
    private static void press() { event(MouseEvent.MOUSE_PRESSED,90,50,true); }
    private static void assertRotation(double degrees) {
        var transform = com.neuronrobotics.bowlerstudio.physics.TransformFactory.nrToAffine(committed);
        var center = transform.transform(12,-8,6);
        near(center.getX(),12,"rotation moved center X");
        near(center.getY(),-8,"rotation moved center Y");
        near(center.getZ(),6,"rotation moved center Z");
        var endpoint = transform.transform(22,-8,6);
        near(endpoint.getX(),12+10*Math.cos(Math.toRadians(degrees)),"rotation X geometry");
        near(endpoint.getY(),-8+10*Math.sin(Math.toRadians(degrees)),"rotation Y geometry");
        near(endpoint.getZ(),6,"rotation Z geometry");
    }
    private static void near(double actual,double expected,String message) {
        require(Math.abs(actual-expected)<1e-5,message+": "+actual+" != "+expected);
    }
    private static void release() { event(MouseEvent.MOUSE_RELEASED,50,10,false); }
    private static void dragQuarterTurn() { event(MouseEvent.MOUSE_DRAGGED,90,50,true);event(MouseEvent.MOUSE_DRAGGED,50,10,true); }
    private static void event(javafx.event.EventType<MouseEvent> type,double x,double y,boolean down) {
        event(circle,type,x,y,down);
    }
    private static void event(ImageView node, javafx.event.EventType<MouseEvent> type,double x,double y,boolean down) {
        Event.fireEvent(node,new MouseEvent(type,x,y,x,y,MouseButton.PRIMARY,1,
            false,false,false,false,down,false,false,true,false,false,new PickResult(node,new Point3D(x,y,0),1)));
    }
    private static void set(Object object,String name,Object value) throws Exception {
        var field=object.getClass().getDeclaredField(name);field.setAccessible(true);field.set(object,value);
    }
    interface Action { void run() throws Exception; }
    private static void fx(Action action) throws Exception {
        FutureTask<Void> task=new FutureTask<>(()->{action.run();return null;});Platform.runLater(task);task.get(30,TimeUnit.SECONDS);
    }
    private static void require(boolean test,String message) { if(!test) throw new AssertionError(message); }
}
