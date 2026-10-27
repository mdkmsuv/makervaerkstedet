import com.commonwealthrobotics.*;
import com.commonwealthrobotics.controls.*;
import com.commonwealthrobotics.fillet.*;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.bowlerstudio.threed.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.*;
import javafx.application.Platform;
import javafx.scene.transform.Affine;
import java.util.*;
import java.util.concurrent.*;

/** Actual native manager run() and installed onSelectEvent callbacks, without a Stage.
 * Workplane picking, renderer construction, selection UI, session executor and native
 * persistence are recording fixtures. This tests callback capture/lifetime/ordering;
 * it does not claim to test picking, surface geometry, disk history or desktop input.
 * submit tasks are held deterministically until the test releases a worker.
 */
public final class SurfaceToolInputProbe {
    static final List<String> failures = new CopyOnWriteArrayList<>();
    static final List<Throwable> asyncErrors = new CopyOnWriteArrayList<>();
    static int cases;
    enum Tool { FILLET, EXTRUDE, SWEEP }
    static class FileFixture extends CaDoodleFile {
        TransformNR plane = new TransformNR();
        @Override public TransformNR getWorkplane() { return plane; }
        @Override public void setWorkplane(TransformNR value) { plane=value; }
        // Prior queue ordering is tested directly. No fake native-running flag
        // masks a missing registration in the shared model edit queue.
        @Override public boolean isOperationRunning() { return false; }
    }
    static class ProjectFixture extends ActiveProject {
        FileFixture file;
        List<CaDoodleOperation> commits;
        CountDownLatch dispatched, completion;
        @Override public CaDoodleFile get() { return file; }
        @Override public Thread addOp(CaDoodleOperation operation) {
            ((AbstractAddFrom)operation).addNameInThisOperation("result");
            commits.add(operation); dispatched.countDown();
            Thread thread=new Thread(() -> {
                try { require(completion.await(8,TimeUnit.SECONDS),"persistence fixture timeout"); }
                catch(InterruptedException e) { throw new AssertionError(e); }
            },"surface-persistence-fixture");
            thread.setDaemon(true); thread.start(); return thread;
        }
    }
    static class CameraFixture extends VirtualCameraMobileBase {
        CameraFixture() { super(null,null,null,"unused"); }
        @Override public TransformNR getCamerFrame() { return new TransformNR(); }
    }
    static class EngineFixture extends BowlerStudio3dEngine {
        VirtualCameraMobileBase cameraFixture;
        EngineFixture() { super("unused"); }
        @Override public VirtualCameraMobileBase getFlyingCamera() { return cameraFixture; }
    }
    static class WorkplaneFixture extends WorkplaneManager {
        Runnable callback;
        TransformNR pose;
        boolean clicked, ground, activeFixture;
        CSG indicator;
        WorkplaneFixture() { super(null,null,null); }
        @Override public void setIndicator(CSG value,Affine ignored) { indicator=value; }
        @Override public void placeWorkplaneVisualization() { }
        @Override public void setOnSelectEvent(Runnable value) { callback=value; }
        @Override public Runnable getOnSelectEvent() { return callback; }
        @Override public void activate(boolean ignored) { activeFixture=true; }
        @Override public boolean isClicked() { return clicked; }
        @Override public boolean isClickOnGround() { return ground; }
        @Override public TransformNR getCurrentAbsolutePose() { return pose; }
        @Override public void setTemporaryPlane() { }
    }
    static class RulerFixture extends RulerManager {
        RulerFixture() { super(null); }
        @Override public void disableRulerMode() { }
    }
    static class SessionFixture extends SelectionSession {
        Queue<Runnable> held;
        List<String> uiSelection;
        volatile SpriteDisplayMode modeFixture;
        SessionFixture() { super(null,null,null); }
        @Override public void submit(Runnable task) { held.add(task); }
        @Override public void setMode(SpriteDisplayMode mode) { modeFixture=mode; }
        @Override public void updateHandleOrientations(TransformNR ignored) { }
        @Override public boolean setKeyBindingFocus() { return true; }
        @Override public void updateControls() { }
        @Override public void save() { }
        @Override public void clearSelection() { nativeSelectionHelper("clearInternalSelection",null); uiSelection.clear(); }
        @Override public void selectAll(Iterable<String> names) {
            // Preserve the native API's additional executor hop. A completion
            // guard followed by this method must not look synchronous in tests.
            submit(() -> selectAllFromCurrentState(names));
        }
        @Override public void selectAllFromCurrentState(Iterable<String> names) {
            nativeSelectionHelper("clearInternalSelection",null);
            uiSelection.clear();
            names.forEach(name -> { nativeSelectionHelper("addToSelected",new Cube(2).toCSG().setName(name)); uiSelection.add(name); });
        }
        void nativeSelectionHelper(String name,CSG shape) {
            try {
                var method=shape==null ? SelectionSession.class.getDeclaredMethod(name) : SelectionSession.class.getDeclaredMethod(name,CSG.class);
                method.setAccessible(true);
                if(shape==null) method.invoke(this); else method.invoke(this,shape);
            } catch(Exception error) { throw new AssertionError("native selection helper failed",error); }
        }
    }
    static class Fixture {
        final Tool tool;
        final ProjectFixture project;
        final SessionFixture session;
        final WorkplaneFixture workplane;
        final RulerFixture ruler;
        final LinkedHashSet<CSG> selected=new LinkedHashSet<>();
        final CSG a=new Cube(10,8,6).toCSG().setName("a"), b=new Cube(6,4,3).toCSG().setName("b");
        Fixture(Tool value) throws Exception {
            tool=value; project=allocate(ProjectFixture.class); project.file=new FileFixture();
            project.commits=new CopyOnWriteArrayList<>(); project.dispatched=new CountDownLatch(1); project.completion=new CountDownLatch(0);
            session=allocate(SessionFixture.class); session.held=new ConcurrentLinkedQueue<>(); session.uiSelection=new CopyOnWriteArrayList<>(List.of("a"));
            setField(session,"selected",selected); setField(session,"meshes",new HashMap<>());
            EngineFixture engine=allocate(EngineFixture.class); engine.cameraFixture=allocate(CameraFixture.class); session.engine=engine;
            workplane=allocate(WorkplaneFixture.class); workplane.pose=pose(11,22,33);
            ruler=allocate(RulerFixture.class); selected.add(a);
        }
        void activate() {
            if(tool==Tool.FILLET) new FilletUIManager().run(selected,project,session,workplane,ruler);
            else new ExtrudeUIManager().run(selected,project,session,workplane,ruler,tool==Tool.SWEEP);
        }
        void callback(boolean click) { workplane.clicked=click; workplane.callback.run(); }
        Thread release() {
            Thread thread=new Thread(() -> {
                try { for(Runnable task; (task=session.held.poll())!=null;) task.run(); }
                catch(Throwable error) { asyncErrors.add(error); error.printStackTrace(); }
            },"surface-session-fixture");
            thread.setDaemon(true); thread.start(); return thread;
        }
        CaDoodleOperation operation() { require(project.commits.size()==1,"expected one operation, got "+project.commits.size()); return project.commits.getFirst(); }
        Set<String> targets() { return operation() instanceof FilletChamfer f ? f.getToFillet() : ((ExtrudeSurface)operation()).getToExtrude(); }
        TransformNR plane() { return operation() instanceof FilletChamfer f ? f.getWorkplane() : ((ExtrudeSurface)operation()).getSlicePlane(); }
    }
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> {});
        try {
            for(Tool tool:Tool.values()) {
                test(tool+"-actual-callback-baseline",() -> {
                    Fixture f=fixture(tool); f.project.completion=new CountDownLatch(1);
                    fx(() -> f.callback(true)); Thread worker=f.release();
                    try {
                        require(f.project.dispatched.await(5,TimeUnit.SECONDS),"baseline did not dispatch");
                        waitForInitialClear(f);
                        fx(() -> {
                            try {
                                var getter=SelectionSession.class.getMethod("getSelectionIntentRevision");
                                long intent=(long)getter.invoke(f.session);
                                f.session.nativeSelectionHelper("restoreSelectedModel",f.a);
                                f.session.nativeSelectionHelper("clearSelectionForRefresh",null);
                                require((long)getter.invoke(f.session)==intent,"native automatic refresh changed selection intent");
                            } catch(NoSuchMethodException baseline) {
                                // update13 displayCurrent drops obsolete names by
                                // clearing this set directly; no intent helper exists.
                                f.session.getSelected().clear();
                            }
                        });
                    } finally { f.project.completion.countDown(); }
                    finish(f,worker);
                    require(f.targets().equals(Set.of("a")),"wrong baseline targets "+f.targets());
                    assertPose(f.plane(),11,22,33);
                    require(f.session.uiSelection.equals(List.of("result")),"normal completion did not select generated result");
                    if(tool!=Tool.FILLET) {
                        var field=ExtrudeSurface.class.getDeclaredField("sweep"); field.setAccessible(true);
                        require(field.getBoolean(f.operation())==(tool==Tool.SWEEP),"wrong sweep mode");
                    }
                });
                test(tool+"-selection-changed-after-click-before-worker",() -> {
                    Fixture f=fixture(tool); fx(() -> { f.callback(true); f.selected.clear(); f.selected.add(f.b); });
                    finish(f,f.release()); require(f.targets().equals(Set.of("a")),"accepted click targets changed to "+f.targets());
                });
                test(tool+"-pose-changed-after-click-before-worker",() -> {
                    Fixture f=fixture(tool); fx(() -> { f.callback(true); f.workplane.pose=pose(44,55,66); });
                    finish(f,f.release()); assertPose(f.plane(),11,22,33);
                });
                test(tool+"-no-click-cancel-callback",() -> {
                    Fixture f=fixture(tool); fx(() -> f.callback(false)); finish(f,f.release());
                    require(f.project.commits.isEmpty(),"callback without click committed");
                });
                test(tool+"-cancel-callback-cannot-borrow-later-click",() -> {
                    Fixture f=fixture(tool);
                    fx(() -> {
                        f.callback(false);
                        // A later placement can reuse this workplane before the
                        // old cancellation callback's held task runs.
                        f.workplane.clicked=true; f.workplane.pose=pose(44,55,66);
                        f.session.setMode(SpriteDisplayMode.PLACING);
                    });
                    finish(f,f.release());
                    require(f.project.commits.isEmpty(),"old no-click cancellation borrowed a later click and committed");
                    require(f.session.modeFixture==SpriteDisplayMode.PLACING,"old cancellation reset a newer placement mode");
                });
                test(tool+"-pending-prior-model-edit-orders-surface",() -> {
                    Fixture f=fixture(tool); CountDownLatch priorStarted=new CountDownLatch(1), allowPrior=new CountDownLatch(1);
                    fx(() -> ModelEditQueue.submit(f.project,() -> {
                        priorStarted.countDown();
                        try { require(allowPrior.await(8,TimeUnit.SECONDS),"prior edit timeout"); }
                        catch(InterruptedException e) { throw new AssertionError(e); }
                    }));
                    require(priorStarted.await(5,TimeUnit.SECONDS),"prior edit never started");
                    boolean overtook;
                    try {
                        fx(() -> f.callback(true)); Thread worker=f.release();
                        overtook=f.project.dispatched.await(150,TimeUnit.MILLISECONDS);
                        allowPrior.countDown(); finish(f,worker); settleQueue(f.project);
                    } finally { allowPrior.countDown(); }
                    require(!overtook,"surface operation overtook pending prior ModelEditQueue edit");
                });
                test(tool+"-completion-preserves-newer-selection",() -> {
                    Fixture f=fixture(tool); f.project.completion=new CountDownLatch(1);
                    fx(() -> f.callback(true)); Thread worker=f.release();
                    try {
                        require(f.project.dispatched.await(5,TimeUnit.SECONDS),"operation did not dispatch");
                        // dispatch latch precedes clearSelection; synchronize with its
                        // completion before simulating a subsequent user selection.
                        waitForInitialClear(f);
                        fx(() -> { f.session.setSelected(new LinkedHashSet<>(List.of(f.b))); f.session.uiSelection.add("b"); });
                    } finally { f.project.completion.countDown(); }
                    finish(f,worker); require(f.session.uiSelection.equals(List.of("b")),"old completion overwrote newer selection with "+f.session.uiSelection);
                });
                test(tool+"-duplicate-accepted-callback-once",() -> {
                    Fixture f=fixture(tool); fx(() -> { f.callback(true); f.callback(true); });
                    finish(f,f.release()); require(f.project.commits.size()==1,"duplicate callback dispatched "+f.project.commits.size()+" operations");
                });
                test(tool+"-project-switched-before-click",() -> {
                    Fixture f=fixture(tool); fx(() -> { f.project.file=new FileFixture(); f.callback(true); });
                    finish(f,f.release()); require(f.project.commits.isEmpty(),"old tool selected models in replacement project");
                });
                test(tool+"-project-switched-before-queued-dispatch",() -> {
                    Fixture f=fixture(tool); CountDownLatch priorStarted=new CountDownLatch(1), allowPrior=new CountDownLatch(1);
                    fx(() -> ModelEditQueue.submit(f.project,() -> {
                        priorStarted.countDown();
                        try { require(allowPrior.await(8,TimeUnit.SECONDS),"prior edit timeout"); }
                        catch(InterruptedException e) { throw new AssertionError(e); }
                    }));
                    require(priorStarted.await(5,TimeUnit.SECONDS),"prior edit never started");
                    try {
                        fx(() -> { f.callback(true); f.project.file=new FileFixture(); });
                        Thread worker=f.release(); allowPrior.countDown(); finish(f,worker);
                    } finally { allowPrior.countDown(); }
                    require(f.project.commits.isEmpty(),"accepted old-project placement dispatched into replacement project");
                });
            }
            require(asyncErrors.isEmpty(),"asynchronous worker errors "+asyncErrors);
            System.out.println("SUMMARY: "+(cases-failures.size())+"/"+cases+" cases passed; "+failures.size()+" failed");
            if(!failures.isEmpty()) throw new AssertionError(String.join("; ",failures));
        } finally { Platform.exit(); }
    }
    static Fixture fixture(Tool tool) throws Exception { Fixture[] f=new Fixture[1]; fx(() -> { f[0]=new Fixture(tool); f[0].activate(); }); return f[0]; }
    static TransformNR pose(double x,double y,double z) { return new TransformNR(x,y,z); }
    static void assertPose(TransformNR p,double x,double y,double z) { require(Math.abs(p.getX()-x)<1e-6 && Math.abs(p.getY()-y)<1e-6 && Math.abs(p.getZ()-z)<1e-6,"accepted pose changed to "+p); }
    static void finish(Fixture fixture,Thread worker) throws Exception { worker.join(5000); require(!worker.isAlive(),"manager worker did not finish"); settleQueue(fixture.project); fx(() -> {}); fx(() -> {}); }
    static void waitForInitialClear(Fixture f) throws Exception { long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5); while(!f.session.uiSelection.isEmpty()) { require(System.nanoTime()<deadline,"initial clear never ran"); Thread.sleep(5); } }
    static void settleQueue(ActiveProject project) throws Exception { long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5); while(ModelEditQueue.isBusy(project)) { fx(() -> {}); require(System.nanoTime()<deadline,"model queue did not settle"); Thread.sleep(5); } }
    static <T> T allocate(Class<T> type) throws Exception { var f=sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); f.setAccessible(true); return type.cast(((sun.misc.Unsafe)f.get(null)).allocateInstance(type)); }
    static void setField(Object object,String name,Object value) throws Exception { var field=SelectionSession.class.getDeclaredField(name); field.setAccessible(true); field.set(object,value); }
    interface Action { void run() throws Exception; }
    static void fx(Action action) throws Exception { FutureTask<Void> task=new FutureTask<>(() -> { action.run(); return null; }); Platform.runLater(task); task.get(15,TimeUnit.SECONDS); }
    static void test(String name,Action action) { cases++; try { action.run(); System.out.println("PASS: "+name); } catch(Throwable e) { failures.add(name+": "+e); System.out.println("FAIL: "+name+": "+e); e.printStackTrace(); } }
    static void require(boolean okay,String message) { if(!okay) throw new AssertionError(message); }
}
