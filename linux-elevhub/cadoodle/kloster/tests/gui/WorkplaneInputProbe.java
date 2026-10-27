import com.commonwealthrobotics.*;
import com.commonwealthrobotics.controls.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine;
import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine.GridHolder;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.geometry.Point3D;
import javafx.scene.input.*;
import javafx.scene.shape.*;
import java.util.*;
import java.util.concurrent.*;

/** Actual workplane activation, event filters, pick geometry and completion.
 * Fixtures replace app/bootstrap, grid artwork and unrelated controls only.
 * No Stage, screen control or physically generated input.
 */
public final class WorkplaneInputProbe {
    static final List<String> failures=new ArrayList<>();
    static final List<Throwable> asyncErrors=new CopyOnWriteArrayList<>();
    static class FileFixture extends CaDoodleFile {
        @Override public TransformNR getWorkplane() { return new TransformNR(); }
    }
    static class ProjectFixture extends ActiveProject {
        CaDoodleFile file;
        @Override public CaDoodleFile get() { return file; }
    }
    static class EngineFixture extends BowlerStudio3dEngine {
        GridHolder ground;
        EngineFixture() { super("unused"); }
        @Override public GridHolder getWorkplaneGroup() { return ground; }
        @Override public void groundToNormal() { }
        @Override public void groundToPicking() { }
        @Override public void addCustomWorkplaneNode(GridHolder grid) { }
        @Override public void placeGrid(TransformNR pose) { }
    }
    static class ControlsFixture extends ControlSprites {
        ControlsFixture() { super(null,null,null,null,null,null,null); }
        @Override public void hideRotationHandles() { }
        @Override public void setMode(SpriteDisplayMode mode) { }
    }
    static class SessionFixture extends SelectionSession {
        Queue<Runnable> tasks;
        HashMap<CSG,MeshHolder> shapes;
        ControlSprites controls;
        SessionFixture() { super(null,null,null); }
        @Override public void submit(Runnable task) { tasks.add(task); }
        @Override public HashMap<CSG,MeshHolder> getMeshes() { return shapes; }
        @Override public ControlSprites getControls() { return controls; }
        @Override public void setMode(SpriteDisplayMode mode) { }
    }
    static class Manager extends WorkplaneManager {
        Manager(ActiveProject project,EngineFixture engine,SessionFixture session) { super(project,engine,session); }
        @Override public GridHolder createTexturedWorkplane(double w,double h) { return grid(); }
    }
    static class Fixture {
        SessionFixture session;
        Manager manager;
        MeshView face;
        TransformNR accepted;
        int commits;
        Fixture() throws Exception {
            ProjectFixture project=allocate(ProjectFixture.class); project.file=new FileFixture();
            EngineFixture engine=allocate(EngineFixture.class); engine.ground=grid();
            session=allocate(SessionFixture.class); session.tasks=new ConcurrentLinkedQueue<>();
            session.shapes=new HashMap<>(); session.controls=allocate(ControlsFixture.class);
            manager=new Manager(project,engine,session);
            face=face();
            // Real event registration normally happens through the mesh map.
            // A known independent face exercises native TriangleMesh normals.
            face.addEventFilter(MouseEvent.ANY,manager);
            manager.setCurrentAbsolutePose(new TransformNR(-20,-30,-40));
            manager.setOnSelectEvent(() -> {
                if(manager.isClicked()) { commits++; accepted=manager.getCurrentAbsolutePose().copy(); }
            });
            manager.activate(true);
        }
        void send(javafx.event.EventType<MouseEvent> type,MouseButton button,double x,double y,double z) {
            Event.fireEvent(face,new MouseEvent(type,1,1,1,1,button,1,false,false,false,false,
                button==MouseButton.PRIMARY,false,button==MouseButton.SECONDARY,true,false,true,
                new PickResult(face,new Point3D(x,y,z),1,0,null)));
        }
        void drain() throws Exception {
            for(int pass=0;pass<12;pass++) {
                Runnable task;
                while((task=session.tasks.poll())!=null) task.run();
                fx(() -> {});
                if(session.tasks.isEmpty()) { fx(() -> {}); if(session.tasks.isEmpty()) return; }
            }
            throw new AssertionError("unbounded fixture task queue");
        }
    }
    public static void main(String[] args) throws Exception {
        Thread.setDefaultUncaughtExceptionHandler((thread,error) -> { asyncErrors.add(error); error.printStackTrace(); });
        Platform.startup(() -> {});
        try {
            test("touch-press-without-hover",() -> {
                Fixture f=fixture(); fx(() -> f.send(MouseEvent.MOUSE_PRESSED,MouseButton.PRIMARY,11,7,5)); f.drain();
                require(f.commits==1,"one tap must complete once"); pose(f.accepted,11,7,5,"tap used stale pose");
            });
            test("press-wins-over-last-hover",() -> {
                Fixture f=fixture(); fx(() -> f.send(MouseEvent.MOUSE_MOVED,MouseButton.NONE,2,3,5)); f.drain();
                pose(f.manager.getCurrentAbsolutePose(),2,3,5,"hover fixture failed");
                fx(() -> f.send(MouseEvent.MOUSE_PRESSED,MouseButton.PRIMARY,15,9,5)); f.drain();
                require(f.commits==1,"changed tap missing"); pose(f.accepted,15,9,5,"press reused last hover");
            });
            test("secondary-press-does-not-confirm",() -> {
                Fixture f=fixture(); fx(() -> f.send(MouseEvent.MOUSE_PRESSED,MouseButton.SECONDARY,11,7,5)); f.drain();
                require(f.commits==0,"secondary button accepted a workplane");
            });
            test("cancel-discards-queued-hover",() -> {
                Fixture f=fixture(); fx(() -> { f.send(MouseEvent.MOUSE_MOVED,MouseButton.NONE,11,7,5); f.manager.cancel(); });
                f.drain(); require(f.commits==0,"cancel confirmed a pose");
                pose(f.manager.getCurrentAbsolutePose(),-20,-30,-40,"cancelled hover changed pose");
            });
            test("inactive-tail-cannot-pick",() -> {
                Fixture f=fixture(); fx(f.manager::cancel); f.drain();
                fx(() -> f.send(MouseEvent.MOUSE_MOVED,MouseButton.NONE,12,8,5)); f.drain();
                pose(f.manager.getCurrentAbsolutePose(),-20,-30,-40,"inactive event changed pose");
            });
            test("explicit-handle-pin-still-confirms",() -> {
                Fixture f=fixture(); fx(() -> {
                    f.manager.setPin(new TransformNR(8,9,10));
                    f.manager.doClickEvent(new MouseEvent(MouseEvent.MOUSE_PRESSED,1,1,1,1,
                        MouseButton.PRIMARY,1,false,false,false,false,true,false,false,true,false,true,
                        new PickResult(f.face,new Point3D(1,1,5),1,0,null)));
                }); f.drain();
                require(f.commits==1,"explicit handle pin did not confirm once");
                pose(f.accepted,8,9,10,"explicit pin was replaced by cursor location");
            });
            test("cancel-before-press-worker-does-not-confirm",() -> {
                Fixture f=fixture(); fx(() -> {
                    f.send(MouseEvent.MOUSE_PRESSED,MouseButton.PRIMARY,11,7,5);
                    f.manager.cancel();
                }); f.drain();
                require(f.commits==0,"pending cancelled pick committed");
            });
            test("reinitialize-discards-old-hover",() -> {
                Fixture f=fixture(); fx(() -> {
                    f.send(MouseEvent.MOUSE_MOVED,MouseButton.NONE,11,7,5);
                    f.manager.cancel(); f.manager.activate(true);
                    f.manager.setCurrentAbsolutePose(new TransformNR(4,6,8));
                }); f.drain();
                pose(f.manager.getCurrentAbsolutePose(),4,6,8,"old session overwrote new placement");
            });
            test("normal-hover-still-works",() -> {
                Fixture f=fixture(); fx(() -> f.send(MouseEvent.MOUSE_MOVED,MouseButton.NONE,11,7,5)); f.drain();
                pose(f.manager.getCurrentAbsolutePose(),11,7,5,"hover stopped working");
                require(f.commits==0,"hover confirmed a placement");
            });
            test("object-press-clears-old-ground-flag",() -> {
                Fixture f=fixture(); fx(() -> {
                    f.manager.setClickOnGround(true);
                    f.send(MouseEvent.MOUSE_PRESSED,MouseButton.PRIMARY,11,7,5);
                }); f.drain();
                require(f.commits==1,"object tap missing");
                require(!f.manager.isClickOnGround(),"object tap borrowed an earlier ground flag");
            });
            require(asyncErrors.isEmpty(),"uncaught async errors: "+asyncErrors);
            System.out.println("SUMMARY: "+(10-failures.size())+"/10 cases passed; "+failures.size()+" failed");
            if(!failures.isEmpty()) throw new AssertionError(String.join("; ",failures));
        } finally { Platform.exit(); }
    }
    static MeshView face() {
        TriangleMesh mesh=new TriangleMesh();
        mesh.getPoints().addAll(0,0,5,40,0,5,0,40,5);
        mesh.getTexCoords().addAll(0,0); mesh.getFaces().addAll(0,0,1,0,2,0);
        return new MeshView(mesh);
    }
    static GridHolder grid() { GridHolder g=new GridHolder(); g.intersectionNode=face(); return g; }
    static Fixture fixture() throws Exception { Fixture[] box=new Fixture[1]; fx(() -> box[0]=new Fixture()); return box[0]; }
    interface Action { void run() throws Exception; }
    static void fx(Action action) throws Exception {
        FutureTask<Void> task=new FutureTask<>(() -> {action.run();return null;}); Platform.runLater(task); task.get(45,TimeUnit.SECONDS);
    }
    static void pose(TransformNR pose,double x,double y,double z,String message) {
        require(pose!=null && Math.abs(pose.getX()-x)<1e-6 && Math.abs(pose.getY()-y)<1e-6 && Math.abs(pose.getZ()-z)<1e-6,
            message+": "+(pose==null?"null":pose.toSimpleString()));
    }
    static <T> T allocate(Class<T> type) throws Exception {
        var f=sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); f.setAccessible(true);
        return type.cast(((sun.misc.Unsafe)f.get(null)).allocateInstance(type));
    }
    static void test(String name,Action task) {
        try { task.run(); System.out.println("PASS: "+name); }
        catch(Throwable error) { failures.add(name+": "+error); System.out.println("FAIL: "+name+": "+error); error.printStackTrace(); }
    }
    static void require(boolean okay,String message) { if(!okay) throw new AssertionError(message); }
}
