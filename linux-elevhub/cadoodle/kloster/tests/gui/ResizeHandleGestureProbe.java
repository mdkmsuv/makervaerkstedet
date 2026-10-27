import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.WorkplaneManager;
import com.commonwealthrobotics.controls.*;
import com.commonwealthrobotics.rotate.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.bowlerstudio.threed.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.*;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.event.EventType;
import javafx.geometry.Point2D;
import javafx.geometry.Point3D;
import javafx.scene.PerspectiveCamera;
import javafx.scene.Node;
import javafx.scene.input.*;
import javafx.scene.layout.Pane;
import javafx.scene.shape.MeshView;
import javafx.scene.transform.Affine;
import java.util.*;
import java.lang.reflect.Field;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicReference;

/** Real nine-handle construction, event filters, preview and save lifecycle.
 * No Stage or desktop input. Projection is deterministic; app construction and
 * selection/display refresh are fixtures. Native Resize processes real CSG.
 * This does not prove camera ray picking, full controller refresh or hardware touch.
 */
public final class ResizeHandleGestureProbe {
    static sun.misc.Unsafe unsafe;
    static class FileFixture extends CaDoodleFile {
        volatile List<CSG> state;
        @Override public List<CSG> getCurrentState() { return state; }
        @Override public TransformNR getWorkplane() { return new TransformNR(); }
        @Override public boolean isOperationRunning() { return false; }
    }
    static class ProjectFixture extends ActiveProject {
        FileFixture file;
        volatile int count;
        CountDownLatch done;
        AtomicReference<Throwable> error;
        @Override public CaDoodleFile get() { return file; }
        @Override public Thread addOp(CaDoodleOperation op) {
            count++;
            Thread thread = new Thread(() -> {
                try {
                    require(op instanceof Resize, "unexpected operation " + op);
                    op.setCaDoodleFile(file);
                    file.state = op.process(file.state);
                } catch (Throwable failure) { error.set(failure); }
                finally { done.countDown(); }
            });
            thread.setDaemon(true); thread.start(); return thread;
        }
    }
    static class SelectionFixture extends SelectionSession {
        ProjectFixture project;
        HashMap<CSG, MeshHolder> meshMap;
        volatile boolean live = true;
        SelectionFixture() { super(null, null, null); }
        @Override public HashMap<CSG, MeshHolder> getMeshes() { return meshMap; }
        @Override public List<CSG> getCurrentStateSelected() { return List.of(project.file.state.getFirst()); }
        @Override public LinkedHashSet<CSG> getSelected() {
            return live ? new LinkedHashSet<>(getCurrentStateSelected()) : new LinkedHashSet<>();
        }
        @Override public List<String> selectedSnapshot() { return List.of("selected"); }
        @Override public Bounds getSellectedBounds() { return project.file.state.getFirst().getBounds(); }
        @Override public void updateControls() { }
        @Override public boolean isLocked() { return false; }
    }
    static class ControlsFixture extends ControlSprites {
        SpriteDisplayMode mode;
        ControlsFixture() { super(null,null,null,null,null,null,null); }
        @Override public void setMode(SpriteDisplayMode value) { mode = value; }
        @Override public void hideRotationHandles() { }
    }
    static class ArrowFixture extends MoveUpArrow {
        ArrowFixture() { super(null,null,null,null,null,null,null); }
        @Override public void resetSelected() { }
    }
    static class EngineFixture extends BowlerStudio3dEngine {
        Pane overlay;
        VirtualCameraMobileBase cameraFixture;
        EngineFixture() { super("unused fixture constructor"); }
        @Override public Pane getOverlayPane() { return overlay; }
        @Override public VirtualCameraMobileBase getFlyingCamera() { return cameraFixture; }
        @Override public Point3D sceneToWorldFixedZ_WP(Point2D p, double z) { return new Point3D(p.getX(), p.getY(), z); }
        @Override public double sceneToWorldFixedXY_WP(Point2D p, double x, double y) { return p.getY(); }
        @Override public double screenToSceneMMscale(Point3D p) { return 1; }
    }
    static class Fixture {
        ProjectFixture project;
        ResizeSessionManager manager;
        ControlsFixture controls;
        MeshView originalMesh;
        Bounds original;
    }
    static final List<String> HANDLES = List.of("rightFront", "rightRear", "leftFront", "leftRear",
            "frontMid", "rearMid", "leftMid", "rightMid", "topCenter");

    public static void main(String[] args) throws Exception {
        var field = sun.misc.Unsafe.class.getDeclaredField("theUnsafe"); field.setAccessible(true);
        unsafe = (sun.misc.Unsafe)field.get(null);
        Platform.startup(() -> {});
        try {
            if (args.length > 0 && args[0].equals("stale-only")) {
                staleCallbacks();
                return;
            }
            for (boolean uniform : List.of(false, true)) for (String name : HANDLES) {
                Fixture f = setup();
                fx(() -> {
                    ResizingHandle h = handle(f, name);
                    Point3D start = h.getAbsolutePositionInWorkplane();
                    double dx = name.contains("Front") || name.equals("frontMid") ? 10 :
                            name.contains("Rear") || name.equals("rearMid") ? -10 : 0;
                    double dy = name.startsWith("left") ? 5 : name.startsWith("right") ? -5 : 0;
                    double sx = start.getX(), sy = name.equals("topCenter") ? start.getZ() : start.getY();
                    double ex = sx + dx, ey = sy + (name.equals("topCenter") ? 4 : dy);
                    event(h, MouseEvent.MOUSE_PRESSED, sx, sy, true, uniform);
                    require(h.isSelected(), name + " did not select real handle");
                    require(get(f.manager,"beingUpdated") == h, name + " press did not initialize manager");
                    event(h, MouseEvent.MOUSE_DRAGGED, ex, ey, true, uniform);
                    require(f.controls.mode == SpriteDisplayMode.Resize, name + " missing Resize mode");
                    require(f.originalMesh.getTransforms().size() == 1, name + " missing preview affine");
                    Bounds expected = expected(f.original, name, uniform);
                    bounds(f.manager.getBoundsDynamic(), expected, name + " dynamic");
                    javafx.geometry.Bounds preview = f.originalMesh.getBoundsInParent();
                    near(preview.getWidth(), expected.getTotalX(), name + " preview X");
                    near(preview.getHeight(), expected.getTotalY(), name + " preview Y");
                    near(preview.getDepth(), expected.getTotalZ(), name + " preview Z");
                    event(h, MouseEvent.MOUSE_RELEASED, ex, ey, false, uniform);
                    event(h, MouseEvent.MOUSE_RELEASED, ex, ey, false, uniform);
                    require(get(f.manager,"beingUpdated") == null && get(f.manager,"originalBounds") == null,
                            name + " left stale interactive state after release");
                    require(!h.manipulator.isMoving(), name + " still dragging after release");
                });
                require(f.project.done.await(5,TimeUnit.SECONDS), name + " no native commit");
                if (f.project.error.get() != null) throw new AssertionError(f.project.error.get());
                require(f.project.count == 1, name + " duplicate native commit");
                bounds(f.project.file.state.getFirst().getBounds(), expected(f.original,name,uniform),name + " native");
                near(f.project.file.state.get(1).getBounds().getTotalX(),6,"unselected object resized");
                System.out.println("PASS: " + name + (uniform ? " uniform" : " independent") + " real press/drag/release, preview, anchor, one native commit");
            }
            Fixture f = setup();
            fx(() -> {
                ResizingHandle h = handle(f,"frontMid"); Point3D p = h.getAbsolutePositionInWorkplane();
                event(h,MouseEvent.MOUSE_PRESSED,p.getX(),p.getY(),true,false);
                event(h,MouseEvent.MOUSE_DRAGGED,p.getX()+1,p.getY(),true,false);
                event(h,MouseEvent.MOUSE_RELEASED,p.getX()+1,p.getY(),false,false);
                require(f.project.count == 0,"tap jitter committed a resize");
                event(h,MouseEvent.MOUSE_PRESSED,p.getX(),p.getY(),true,false);
                event(h,MouseEvent.MOUSE_DRAGGED,p.getX()+10,p.getY(),true,false);
                f.manager.abortInteraction();
                event(h,MouseEvent.MOUSE_RELEASED,p.getX()+10,p.getY(),false,false);
                require(get(f.manager,"beingUpdated") == null && get(f.manager,"originalBounds") == null,"abort left gesture state");
                require(f.originalMesh.getTransforms().isEmpty(),"abort left scaled preview");
                for (String name : HANDLES) require(!handle(f,name).isSelected(),"abort left selected " + name);
                f.manager.setResizeAllowed(false,true);
                event(h,MouseEvent.MOUSE_PRESSED,p.getX(),p.getY(),true,false);
                event(h,MouseEvent.MOUSE_DRAGGED,p.getX()+10,p.getY(),true,false);
                event(h,MouseEvent.MOUSE_RELEASED,p.getX()+10,p.getY(),false,false);
            });
            require(!f.project.done.await(200,TimeUnit.MILLISECONDS) && f.project.count == 0,"cancel/locked drag committed");
            System.out.println("PASS: real resize handle jitter, cancellation and locked drag do not commit");
            staleCallbacks();
        } finally { Platform.exit(); }
    }
    static void staleCallbacks() throws Exception {
        Fixture stale = setup();
        SelectionFixture session = (SelectionFixture) get(stale.manager, "session");
        CountDownLatch entered = new CountDownLatch(1), queued = new CountDownLatch(1);
        FutureTask<Void> blocked = new FutureTask<>(() -> {
            entered.countDown();
            require(queued.await(10, TimeUnit.SECONDS), "camera callbacks were not queued");
            session.live = false;
            stale.controls.clearSelection();
            return null;
        });
        Platform.runLater(blocked);
        require(entered.await(5, TimeUnit.SECONDS), "FX blocker did not start");
        try {
            // BowlerKernel runs immediately on FX; enqueue from the camera
            // thread while FX is held, then deselect before these callbacks.
            stale.manager.threeDTarget(1024, 768, -1000, stale.original, new TransformNR(), false);
        } finally { queued.countDown(); }
        blocked.get(10, TimeUnit.SECONDS);
        fx(() -> {
            for (String name : HANDLES)
                require(!handle(stale, name).getMesh().isVisible(), "stale resize callback revived " + name);
        });
        System.out.println("PASS: real resize callbacks stay hidden after selection clears before FX drain");
    }

    static Fixture setup() throws Exception {
        Fixture f = new Fixture();
        fx(() -> {
            f.project = allocate(ProjectFixture.class); f.project.file = new FileFixture();
            f.project.file.state = List.of(new Cube(20,10,8).toCSG().movex(30).movey(20).movez(14).setName("selected"),
                    new Cube(6).toCSG().movex(100).setName("other"));
            f.project.done = new CountDownLatch(1); f.project.error = new AtomicReference<>();
            f.original = f.project.file.state.getFirst().getBounds();
            SelectionFixture session = allocate(SelectionFixture.class); session.project=f.project; session.meshMap=new HashMap<>(); session.live=true;
            CSG shape = f.project.file.state.getFirst(); f.originalMesh=shape.newMesh();
            session.meshMap.put(shape,session.new MeshHolder(f.originalMesh,shape.newMesh(true),f.original));
            EngineFixture engine = allocate(EngineFixture.class); engine.overlay=new Pane();
            engine.cameraFixture=allocate(VirtualCameraMobileBase.class);
            set(engine.cameraFixture,"camera",new PerspectiveCamera(true));
            WorkplaneManager workplane=allocate(WorkplaneManager.class);
            f.controls=allocate(ControlsFixture.class);
            set(f.controls, "ap", f.project);
            set(f.controls, "allElems", new ArrayList<Node>());
            set(f.controls, "selectionLive", true);
            set(f.controls, "session", session);
            ArrowFixture arrow=allocate(ArrowFixture.class);
            f.manager=new ResizeSessionManager(engine,new Affine(),()->{},f.project,session,new Affine(),
                    arrow,f.controls,workplane);
            // The fixture bypasses ControlSprites' production constructor.
            // Populate the three reset collaborators used by clearSelection().
            set(f.controls, "scaleSession", f.manager);
            set(f.controls, "upArrow", arrow);
            RotationSessionManager rotations=allocate(RotationSessionManager.class);
            set(rotations, "handles", new ArrayList<RotationHandle>());
            set(f.controls, "rotationManager", rotations);
            f.manager.setResizeAllowed(true,false); f.manager.setSnapGrid(1);
            f.manager.threeDTarget(1024,768,-1000,f.original,new TransformNR(),false);
            ArrayList<Node> handleNodes = new ArrayList<>();
            for (String name : HANDLES)
                handleNodes.add(handle(f, name).getMesh());
            set(f.controls, "allElems", handleNodes);
        });
        fx(() -> {}); // flush actual handles' deferred positions before input
        return f;
    }
    static Bounds expected(Bounds b,String h,boolean uniform) {
        double x=b.getTotalX(), y=b.getTotalY(), z=b.getTotalZ();
        double ax=h.contains("Front") || h.equals("frontMid") ? 0 : h.contains("Rear") || h.equals("rearMid") ? 1 : .5;
        double ay=h.startsWith("left") ? 0 : h.startsWith("right") ? 1 : .5;
        double nx=uniform || ax!=.5 ? x*1.5 : x;
        double ny=uniform || ay!=.5 ? y*1.5 : y;
        double nz=uniform || h.equals("topCenter") ? z*1.5 : z;
        return new Bounds(Vector3d.xyz(b.getMinX()-(nx-x)*ax,b.getMinY()-(ny-y)*ay,b.getMinZ()),
                Vector3d.xyz(b.getMaxX()+(nx-x)*(1-ax),b.getMaxY()+(ny-y)*(1-ay),b.getMinZ()+nz));
    }
    static ResizingHandle handle(Fixture f,String name) throws Exception { return (ResizingHandle)get(f.manager,name); }
    static Object get(Object o,String name) throws Exception {
        Class<?> type=o.getClass(); Field f=null;
        while(type!=null && f==null){try{f=type.getDeclaredField(name);}catch(NoSuchFieldException missing){type=type.getSuperclass();}}
        if(f==null)throw new NoSuchFieldException(name);
        f.setAccessible(true);return f.get(o);
    }
    static void set(Object o,String name,Object value) throws Exception {
        Class<?> type=o.getClass(); Field f=null;
        while(type!=null && f==null){try{f=type.getDeclaredField(name);}catch(NoSuchFieldException missing){type=type.getSuperclass();}}
        if(f==null)throw new NoSuchFieldException(name);
        f.setAccessible(true);f.set(o,value);
    }
    static <T>T allocate(Class<T> type) throws Exception { return type.cast(unsafe.allocateInstance(type)); }
    static void event(ResizingHandle h,EventType<MouseEvent> type,double x,double y,boolean down,boolean shift) {
        Event.fireEvent(h.getMesh(),new MouseEvent(type,x,y,x,y,MouseButton.PRIMARY,1,
                shift,false,false,false,down,false,false,true,false,false,new PickResult(h.getMesh(),Point3D.ZERO,1)));
    }
    static void bounds(Bounds a,Bounds e,String m) { near(a.getMinX(),e.getMinX(),m+" minX");near(a.getMaxX(),e.getMaxX(),m+" maxX");
        near(a.getMinY(),e.getMinY(),m+" minY");near(a.getMaxY(),e.getMaxY(),m+" maxY");near(a.getMinZ(),e.getMinZ(),m+" minZ");near(a.getMaxZ(),e.getMaxZ(),m+" maxZ"); }
    static void near(double a,double e,String m) { require(Math.abs(a-e)<1e-4,m+": "+a+" != "+e); }
    static void require(boolean b,String m) { if(!b)throw new AssertionError(m); }
    interface Action { void run() throws Exception; }
    static void fx(Action a) throws Exception {FutureTask<Void> t=new FutureTask<>(()->{a.run();return null;});Platform.runLater(t);t.get(20,TimeUnit.SECONDS);}
}
