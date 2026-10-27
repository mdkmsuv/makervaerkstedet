import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.MainController;
import com.commonwealthrobotics.controls.SelectionSession;
import com.commonwealthrobotics.kloster.ModelEditQueue;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.*;
import com.neuronrobotics.sdk.addons.kinematics.math.TransformNR;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import javafx.application.Platform;
import java.lang.reflect.Field;
import java.nio.file.Files;
import java.util.List;
import java.util.concurrent.*;

/** Real toolbar Undo/Redo handlers, real history; no Stage or desktop input. */
public final class HistoryOrderProbe {
    static class Project extends ActiveProject {
        CaDoodleFile file;
        @Override public CaDoodleFile get() { return file; }
    }
    static class Session extends SelectionSession {
        Session() { super(null,null,null); }
        @Override public void abortInteraction() { }
        @Override public boolean setKeyBindingFocus() { return true; }
    }
    static class Fixture extends CaDoodleOperation {
        public String getType() { return "HistoryOrderFixture"; }
        public List<CSG> process(List<CSG> previous) {
            return List.of(new Cube(10,10,10).toCSG().setName("Cube"));
        }
        public List<String> getNamesAddedInThisOperation() { return List.of("Cube"); }
    }
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> {});
        try {
            CaDoodleFile.setImageEngine((db,shapes,path) -> new javafx.scene.image.WritableImage(1,1));
            var root = Files.createTempDirectory("cadoodle-history-order-");
            var file = new CaDoodleFile().setSelf(root.resolve("history.doodle").toFile());
            file.initialize(); join(file.addOperation(new Fixture()));
            Field unsafeField = sun.misc.Unsafe.class.getDeclaredField("theUnsafe");
            unsafeField.setAccessible(true);
            var unsafe = (sun.misc.Unsafe) unsafeField.get(null);
            var project = (Project) unsafe.allocateInstance(Project.class); project.file = file;
            var controller = (MainController) unsafe.allocateInstance(MainController.class);
            set(controller,"ap",project);
            var session = (Session) unsafe.allocateInstance(Session.class);
            var legacyExecutor = Executors.newSingleThreadExecutor(task -> {
                var worker = new Thread(task,"history-probe-session"); worker.setDaemon(true); return worker;
            });
            var executorField = SelectionSession.class.getDeclaredField("executor");
            executorField.setAccessible(true); executorField.set(session,legacyExecutor);
            set(controller,"session",session);
            var entered = new CountDownLatch(1); var release = new CountDownLatch(1);
            fx(() -> {
                ModelEditQueue.submit(project, () -> { entered.countDown(); await(release); });
                ModelEditQueue.submit(project, () -> {
                    try { join(file.addOperation(new MoveCenter().setLocation(new TransformNR(17,-9,0))
                            .setNames(List.of("Cube"),file))); }
                    catch (Exception error) { throw new AssertionError(error); }
                });
                invoke(controller,"onUndo");
            });
            require(entered.await(5,TimeUnit.SECONDS),"queue blocker did not start");
            legacyExecutor.submit(() -> {}).get(5,TimeUnit.SECONDS);
            require(file.getCurrentIndex()==1,"Undo bypassed the pending model edit");
            release.countDown(); idle(project);
            near(file.getCurrentState().getFirst().getCenterX(),0,"Undo did not undo the queued move");
            require(file.isForwardAvailable(),"Undo lost redo history");
            fx(() -> invoke(controller,"onRedo")); idle(project);
            near(file.getCurrentState().getFirst().getCenterX(),17,"Redo did not restore move X");
            near(file.getCurrentState().getFirst().getCenterY(),-9,"Redo did not restore move Y");

            // A queued action belongs to the project open at the original click.
            var other = new CaDoodleFile().setSelf(root.resolve("other.doodle").toFile());
            other.initialize(); join(other.addOperation(new Fixture()));
            var switchGate = new CountDownLatch(1);
            fx(() -> {
                ModelEditQueue.submit(project, () -> await(switchGate));
                invoke(controller,"onUndo");
                project.file = other;
            });
            switchGate.countDown(); idle(project);
            require(file.getCurrentIndex()==2 && other.getCurrentIndex()==1,
                    "queued history changed a project after switching");
            file.close(); other.close();
            legacyExecutor.shutdownNow();
            System.out.println("PASS: real toolbar Undo/Redo follow pending move and guard project identity");
        } finally { Platform.exit(); }
    }
    private static void invoke(MainController controller,String method) throws Exception {
        var callback = MainController.class.getDeclaredMethod(method,javafx.event.ActionEvent.class);
        callback.setAccessible(true); callback.invoke(controller,new javafx.event.ActionEvent());
    }
    private static void set(Object target,String name,Object value) throws Exception {
        var field=target.getClass().getDeclaredField(name); field.setAccessible(true); field.set(target,value);
    }
    interface Action { void run() throws Exception; }
    private static void fx(Action action) throws Exception {
        var task = new FutureTask<Void>(() -> { action.run(); return null; });
        Platform.runLater(task); task.get(10,TimeUnit.SECONDS);
    }
    private static void join(Thread task) throws InterruptedException {
        require(task!=null,"native operation was not submitted"); task.join(10_000);
        require(!task.isAlive(),"native operation timed out");
    }
    private static void await(CountDownLatch latch) {
        try { require(latch.await(10,TimeUnit.SECONDS),"queue blocker timed out"); }
        catch (InterruptedException error) { Thread.currentThread().interrupt(); throw new AssertionError(error); }
    }
    private static void idle(Project project) throws InterruptedException {
        long deadline=System.nanoTime()+10_000_000_000L;
        while (ModelEditQueue.isBusy(project)) {
            require(System.nanoTime()<deadline,"model queue did not settle"); Thread.sleep(10);
        }
    }
    private static void near(double actual,double expected,String message) {
        require(Math.abs(actual-expected)<1e-5,message);
    }
    private static void require(boolean value,String message) { if(!value) throw new AssertionError(message); }
}
