import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.mirror.MirrorSessionManager;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.bowlerstudio.threed.BowlerStudio3dEngine;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.*;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.geometry.Point3D;
import javafx.scene.Node;
import javafx.scene.input.*;
import javafx.scene.shape.MeshView;
import javafx.scene.transform.Affine;
import java.util.*;
import java.util.concurrent.*;

/** Real mirror manager, geometry, preview and node filters; no Stage or desktop input.
 * Only app/renderer construction and addOp persistence dispatch are fixtures.
 * Native history/persistence is covered separately by ManipulationHistoryProbe.
 * All failed assertions remain failures; independent cases run to collect evidence.
 */
public final class MirrorInputProbe {
    static final List<String> failures = new CopyOnWriteArrayList<>();
    static final List<Throwable> asyncErrors = new CopyOnWriteArrayList<>();
    static class FileFixture extends CaDoodleFile {
        @Override public TransformNR getWorkplane() { return new TransformNR(); }
    }
    static class ProjectFixture extends ActiveProject {
        CaDoodleFile file;
        List<Mirror> commits;
        List<ICaDoodleStateUpdate> listeners;
        CountDownLatch dispatched, completion;
        volatile Thread handleWorker;
        @Override public CaDoodleFile get() { return file; }
        @Override public ActiveProject addListener(ICaDoodleStateUpdate listener) { listeners.add(listener); return this; }
        @Override public ActiveProject removeListener(ICaDoodleStateUpdate listener) { listeners.remove(listener); return this; }
        @Override public Thread addOp(CaDoodleOperation operation) {
            handleWorker = Thread.currentThread();
            commits.add((Mirror)operation); dispatched.countDown();
            Thread worker = new Thread(() -> {
                try { require(completion.await(8, TimeUnit.SECONDS), "operation fixture timeout"); }
                catch (InterruptedException e) { throw new AssertionError(e); }
            }, "mirror-persistence-fixture");
            worker.setDaemon(true); worker.start(); return worker;
        }
    }
    static class EngineFixture extends BowlerStudio3dEngine {
        List<Node> nodes;
        EngineFixture() { super("unused"); }
        @Override public void addUserNode(Node node) { require(Platform.isFxApplicationThread(), "preview add off FX"); nodes.add(node); }
        @Override public void removeUserNode(Node node) { require(Platform.isFxApplicationThread(), "preview removal off FX"); nodes.remove(node); }
    }
    static class Fixture {
        ProjectFixture project;
        EngineFixture engine;
        MirrorSessionManager manager;
        List<CSG> shapes;
        Fixture() throws Exception {
            project = allocate(ProjectFixture.class); project.file = new FileFixture();
            project.commits = new CopyOnWriteArrayList<>(); project.listeners = new CopyOnWriteArrayList<>();
            project.dispatched = new CountDownLatch(1); project.completion = new CountDownLatch(0);
            engine = allocate(EngineFixture.class); engine.nodes = new CopyOnWriteArrayList<>();
            // Asymmetry in all three directions makes axis mixups observable.
            CSG asymmetric = new Cube(12, 8, 6).toCSG().union(new Cube(4, 3, 2).toCSG().movex(6).movey(3).movez(3));
            shapes = List.of(asymmetric.movex(18).setName("a").setManipulator(new Affine()),
                asymmetric.movey(25).movez(12).setName("b").setManipulator(new Affine()));
            manager = new MirrorSessionManager(new Affine(), project, null, new Affine());
        }
        void initialize() {
            initialize(List.of("a", "b"));
        }
        void initialize(List<String> selected) {
            manager.initialize(shapes.getFirst().union(shapes.get(1)).getBounds(), engine,
                shapes, new ArrayList<>(selected), new HashMap<>());
        }
        MeshView handle(int axis) { return (MeshView)manager.getElements().get(axis); }
        void cancel() { manager.cancel(); }
    }
    public static void main(String[] args) throws Exception {
        Thread.setDefaultUncaughtExceptionHandler((thread,error) -> { asyncErrors.add(error); error.printStackTrace(); });
        Platform.startup(() -> {});
        try {
            for (int axis = 0; axis < 3; axis++) {
                final int chosen = axis;
                test("axis-"+axis+"-click-multiselection-preview", () -> axisCase(chosen));
            }
            test("cancel-then-delivered-click", () -> {
                Fixture f = fixture();
                fx(() -> { f.cancel(); click(f.handle(0), MouseButton.PRIMARY, true); });
                settle(); require(f.project.commits.isEmpty(), "click delivered in cancellation turn committed");
            });
            test("cancel-removes-filters-and-previews", () -> {
                Fixture f = fixture(); fx(f::cancel); settle();
                fx(() -> { click(f.handle(0), MouseButton.PRIMARY, true); enter(f.handle(0)); });
                settle(); require(f.project.commits.isEmpty(), "settled cancel left click filter");
                require(f.engine.nodes.isEmpty(), "settled cancel left preview nodes");
                require(f.project.listeners.isEmpty(), "cancel left project listeners");
                require(!f.manager.isActive(), "cancel left active manager");
            });
            test("pending-operation-completion-after-cancel", () -> {
                Fixture f = fixture(); f.project.completion = new CountDownLatch(1);
                fx(() -> click(f.handle(0), MouseButton.PRIMARY, true));
                require(f.project.dispatched.await(5, TimeUnit.SECONDS), "click never reached addOp");
                fx(f::cancel); settle(); require(f.engine.nodes.isEmpty(), "cancel did not initially clear preview");
                f.project.completion.countDown();
                // Join the real MirrorHandle worker, including its post-join
                // updateState(), before inspecting the FX preview installation.
                waitForHandleCompletion(f);
                settle();
                require(f.project.commits.size()==1, "completion changed commit count");
                require(f.engine.nodes.isEmpty(), "completed old mirror recreated previews after cancellation");
            });
            test("queued-preview-install-after-cancel", () -> {
                Fixture f=fixture();
                var field=MirrorSessionManager.class.getDeclaredField("x"); field.setAccessible(true);
                var handle=(com.commonwealthrobotics.mirror.MirrorHandle)field.get(f.manager);
                CountDownLatch blocked=new CountDownLatch(1), allow=new CountDownLatch(1);
                Platform.runLater(() -> {
                    blocked.countDown();
                    try { require(allow.await(5,TimeUnit.SECONDS),"blocked FX timeout"); f.cancel(); }
                    catch(InterruptedException error) { throw new AssertionError(error); }
                });
                require(blocked.await(5,TimeUnit.SECONDS),"FX not blocked");
                try {
                    // Actual project-state listener entry point computes and
                    // queues an installer while the FX thread is blocked.
                    handle.onWorkplaneChange(new TransformNR());
                } finally { allow.countDown(); }
                settle(); require(f.engine.nodes.isEmpty(),"queued pre-cancel installer recreated previews");
                require(f.project.commits.isEmpty(),"workplane preview refresh committed geometry");
            });
            test("secondary-click-does-not-edit", () -> {
                Fixture f = fixture(); fx(() -> click(f.handle(0), MouseButton.SECONDARY, false));
                settle(); require(f.project.commits.isEmpty(), "secondary click committed a mirror");
            });
            test("reinitialize-before-old-operation-completes", () -> {
                Fixture f = fixture(); f.project.completion = new CountDownLatch(1);
                fx(() -> click(f.handle(0), MouseButton.PRIMARY, true));
                require(f.project.dispatched.await(5, TimeUnit.SECONDS), "old click never dispatched");
                Mirror accepted = f.project.commits.getFirst();
                fx(() -> { f.cancel(); f.initialize(List.of("b")); }); settle();
                List<Node> currentPreview = List.copyOf(f.engine.nodes);
                require(currentPreview.size()==6, "new session previews missing");
                f.project.completion.countDown(); waitForHandleCompletion(f); settle();
                require(accepted.getNamesAddedInThisOperation().equals(List.of("a", "b"))
                    && accepted.getLocation()==MirrorOrientation.x,
                    "reinitialization changed already accepted operation selection/axis");
                require(f.engine.nodes.size()==currentPreview.size() && f.engine.nodes.containsAll(currentPreview),
                    "old operation completion replaced current-session preview nodes");
                fx(f::cancel); settle();
            });
            test("reinitialize-does-not-multiply-filters", () -> {
                Fixture f = fixture(); fx(f::initialize); settle();
                fx(() -> click(f.handle(2), MouseButton.PRIMARY, true));
                require(f.project.dispatched.await(5, TimeUnit.SECONDS), "reinitialized click missing");
                settle(); require(f.project.commits.size()==1, "reinitialization multiplied click commits");
                fx(f::cancel); settle();
            });
            test("mirror-registers-and-orders-queued-followup", () -> {
                Fixture f=fixture(); f.project.completion=new CountDownLatch(1);
                fx(() -> click(f.handle(0),MouseButton.PRIMARY,true));
                require(f.project.dispatched.await(5,TimeUnit.SECONDS),"mirror did not dispatch");
                boolean registered=ModelEditQueue.isBusy(f.project);
                CountDownLatch followupDone=new CountDownLatch(1);
                boolean[] early={false};
                fx(() -> ModelEditQueue.submit(f.project,() -> {
                    early[0]=f.project.completion.getCount()!=0; followupDone.countDown();
                }));
                // Give an incorrectly independent queue a bounded opportunity
                // to run while the accepted mirror operation is still pending.
                followupDone.await(150,TimeUnit.MILLISECONDS);
                f.project.completion.countDown(); waitForHandleCompletion(f);
                require(followupDone.await(5,TimeUnit.SECONDS),"queued followup did not finish"); settle();
                require(registered,"accepted mirror absent from ModelEditQueue while pending");
                require(!early[0],"queued followup overtook pending mirror");
                fx(f::cancel); settle();
            });
            require(asyncErrors.isEmpty(), "uncaught asynchronous errors: "+asyncErrors.size());
            System.out.println("SUMMARY: "+(11-failures.size())+"/11 cases passed; "+failures.size()+" failed");
            if (!failures.isEmpty()) throw new AssertionError(String.join("; ", failures));
        } finally { Platform.exit(); }
    }
    static void axisCase(int axis) throws Exception {
        Fixture f = fixture();
        require(f.manager.isActive(), "manager not active");
        require(f.engine.nodes.size()==6, "expected two previews per axis, got "+f.engine.nodes.size());
        fx(() -> {
            require(f.engine.nodes.stream().noneMatch(Node::isVisible), "preview initially visible");
            enter(f.handle(axis));
            require(f.engine.nodes.stream().filter(Node::isVisible).count()==2, "hover did not show exactly selected-axis previews");
            require(f.engine.nodes.stream().allMatch(Node::isMouseTransparent), "preview intercepts object input");
            Event.fireEvent(f.handle(axis), event(MouseEvent.MOUSE_EXITED, f.handle(axis), MouseButton.NONE, false));
            require(f.engine.nodes.stream().noneMatch(Node::isVisible), "exit left preview visible");
            click(f.handle(axis), MouseButton.PRIMARY, axis!=1);
        });
        require(f.project.dispatched.await(5, TimeUnit.SECONDS), "mirror never dispatched");
        settle(); require(f.project.commits.size()==1, "one click must dispatch once");
        Mirror op = f.project.commits.getFirst();
        require(op.getLocation()==MirrorOrientation.values()[axis], "wrong committed mirror axis");
        require(op.getNamesAddedInThisOperation().equals(List.of("a","b")), "selection missing or reordered");
        CSG other = new Cube(5).toCSG().movex(-50).setName("other").setManipulator(new Affine());
        List<CSG> incoming = new ArrayList<>(f.shapes); incoming.add(other);
        List<CSG> processed = op.process(incoming);
        require(processed.size()==3, "mirror changed object count");
        for (CSG shape : f.shapes) {
            CSG actual = processed.stream().filter(c -> c.getName().equals(shape.getName())).findFirst().orElseThrow();
            reflectedVertices(shape, actual, axis);
        }
        require(processed.stream().filter(c -> c.getName().equals("other")).findFirst().orElseThrow()==other,
            "unselected shape changed");
        fx(f::cancel); settle();
    }
    static void reflectedVertices(CSG original, CSG reflected, int axis) {
        double[] input=original.getVertices(), output=reflected.getVertices();
        require(input.length==output.length, "mirror changed vertex count");
        double[] center={original.getCenterX(),original.getCenterY(),original.getCenterZ()};
        for (int i=0; i<input.length; i+=3) {
            double[] point={input[i],input[i+1],input[i+2]}; point[axis]=2*center[axis]-point[axis];
            boolean found=false;
            for(int j=0;j<output.length;j+=3) if(Math.abs(output[j]-point[0])<1e-5 && Math.abs(output[j+1]-point[1])<1e-5 && Math.abs(output[j+2]-point[2])<1e-5) { found=true; break; }
            require(found,"mirror axis "+axis+" missing expected reflected vertex "+Arrays.toString(point));
        }
    }
    static Fixture fixture() throws Exception {
        Fixture[] box=new Fixture[1]; fx(() -> box[0]=new Fixture()); settle();
        fx(box[0]::initialize); settle(); return box[0];
    }
    static void enter(MeshView node) { Event.fireEvent(node,event(MouseEvent.MOUSE_ENTERED,node,MouseButton.NONE,false)); }
    static void click(MeshView node,MouseButton button,boolean synthesized) { Event.fireEvent(node,event(MouseEvent.MOUSE_CLICKED,node,button,synthesized)); }
    static MouseEvent event(javafx.event.EventType<MouseEvent> type,MeshView node,MouseButton button,boolean synthesized) {
        return new MouseEvent(type,1,1,1,1,button,1,false,false,false,false,false,false,false,
            synthesized,false,true,new PickResult(node,new Point3D(1,1,0),1));
    }
    static <T> T allocate(Class<T> type) throws Exception {
        var field=sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); field.setAccessible(true);
        return type.cast(((sun.misc.Unsafe)field.get(null)).allocateInstance(type));
    }
    interface Action { void run() throws Exception; }
    static void fx(Action action) throws Exception {
        FutureTask<Void> task=new FutureTask<>(() -> { action.run(); return null; }); Platform.runLater(task); task.get(30,TimeUnit.SECONDS);
    }
    static void settle() throws Exception { Thread.sleep(80); fx(() -> {}); fx(() -> {}); }
    static void waitForHandleCompletion(Fixture f) throws Exception {
        if (!(f.project.handleWorker instanceof ForkJoinWorkerThread)) {
            // Baseline uses a dedicated MirrorHandle thread; retain the exact join.
            f.project.handleWorker.join(5000);
            require(!f.project.handleWorker.isAlive(),"mirror handle worker did not finish");
        }
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(5);
        while(ModelEditQueue.isBusy(f.project)) {
            fx(() -> {});
            require(System.nanoTime()<deadline,"model queue did not finish"); Thread.sleep(10);
        }
        fx(() -> {});
    }
    static void test(String name,Action action) {
        try { action.run(); System.out.println("PASS: "+name); }
        catch(Throwable error) { failures.add(name+": "+error); System.out.println("FAIL: "+name+": "+error); error.printStackTrace(); }
    }
    static void require(boolean okay,String message) { if(!okay) throw new AssertionError(message); }
}
