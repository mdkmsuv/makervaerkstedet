import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.MainController;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.lang.reflect.Field;
import java.util.concurrent.*;
import javafx.application.Platform;
import javafx.scene.control.TextField;
import sun.misc.Unsafe;

/** A delayed title edit must never address the next pupil's model. No Stage. */
public final class ProjectTitleFinishProbe {
    private static final class Project extends ActiveProject {
        CaDoodleFile current = new CaDoodleFile().setProjectName("Forrige projekt");
        @Override public CaDoodleFile get() { return current; }
    }
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> { });
        try {
            Field unsafe = Unsafe.class.getDeclaredField("theUnsafe"); unsafe.setAccessible(true);
            MainController controller = (MainController) ((Unsafe) unsafe.get(null)).allocateInstance(MainController.class);
            Project project = new Project();
            CompletableFuture<Void> done = new CompletableFuture<>();
            Platform.runLater(() -> {
                try {
                    Field active = MainController.class.getDeclaredField("ap"); active.setAccessible(true); active.set(controller, project);
                    Field box = MainController.class.getDeclaredField("fileNameBox"); box.setAccessible(true); box.set(controller, new TextField("Gammel forsinket tekst"));
                    var method = MainController.class.getDeclaredMethod("onNameTyped"); method.setAccessible(true); method.invoke(controller);
                    project.current = new CaDoodleFile().setProjectName("Ny elevs projekt");
                    done.complete(null);
                } catch (Throwable failure) { done.completeExceptionally(failure); }
            });
            done.get(10, TimeUnit.SECONDS);
            Thread.sleep(3400); // Also catches the previous upstream 3-second worker.
            if (!project.get().getMyProjectName().equals("Ny elevs projekt")) throw new AssertionError("stale title callback modified the new model");
            System.out.println("PASS: delayed controller title callback cannot modify the next project");
        } finally { Platform.exit(); }
    }
}
