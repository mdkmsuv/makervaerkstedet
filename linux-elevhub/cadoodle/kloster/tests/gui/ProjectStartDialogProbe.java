import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.kloster.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.nio.file.*;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.function.BooleanSupplier;
import javafx.application.Platform;
import javafx.scene.Scene;
import javafx.scene.control.*;
import javafx.scene.layout.*;
import javafx.stage.*;

/** Synthetic start choices on a private Xvfb display, including hidden prewarm. */
public final class ProjectStartDialogProbe {
    private static final class File extends CaDoodleFile { @Override public boolean isInitialized() { return true; } }
    private static final class Project extends ActiveProject {
        CaDoodleFile file = new File().setSelf(Path.of(System.getProperty("user.home"), "draft.doodle").toFile());
        @Override public CaDoodleFile get() { return file; }
        @Override public boolean hasUnsavedChanges() { return false; }
        @Override public boolean prepareProjectClose(boolean save) { return true; }
        @Override public void resetProjectAfterFinish() { file = new File().setSelf(Path.of(System.getProperty("user.home"), "fresh.doodle").toFile()); }
    }
    private static Stage owner;
    private static Pane root;
    private static Project project;
    public static void main(String[] args) throws Exception {
        Path projects = Files.createTempDirectory("start-probe-").resolve("Projekter");
        System.setProperty("elevhub.projects", projects.toString());
        java.lang.reflect.Field prewarm = BackgroundPrewarm.class.getDeclaredField("prewarming"); prewarm.setAccessible(true); prewarm.setBoolean(null, true);
        Platform.startup(() -> Platform.setImplicitExit(false));
        try {
            fx(() -> {
                project = new Project(); root = new Pane(); owner = new Stage();
                ProjectCloseGuard.install(root, project); ProjectStartDialog.install(root, project);
                owner.setScene(new Scene(root, 600, 400)); owner.show();
            });
            Thread.sleep(650); require(!hasDialog(), "hidden prewarm opened an entry dialog");
            prewarm.setBoolean(null, false); await(ProjectStartDialogProbe::hasDialog);
            fx(() -> choose("Start nyt projekt")); require(hasDialog() && root.isDisabled(), "invalid entry allowed editing");
            fx(() -> {
                VBox content = (VBox) popup().getContent(); GridPane form = (GridPane) content.getChildren().get(0);
                var fields = form.getChildren().stream().filter(node -> node instanceof TextField).map(node -> (TextField) node).toList();
                ComboBox<?> classes = (ComboBox<?>) form.getChildren().stream().filter(node -> node instanceof ComboBox).findFirst().orElseThrow();
                classes.getEditor().setText("5.B"); fields.get(0).setText("Alma"); fields.get(1).setText("Min bro"); choose("Start nyt projekt");
            });
            await(() -> !hasDialog()); require(!root.isDisabled() && ProjectNaming.name(project.get()).firstName().equals("Alma"), "entry did not persist the round's pupil name");
            require(!Files.exists(projects), "naming published an empty project before explicit save");
            fx(() -> { project.resetProjectAfterFinish(); ProjectStartDialog.afterFinish(root); });
            Thread.sleep(650); require(!hasDialog(), "finish immediately reopened entry before next activation");
            fx(() -> ProjectStartDialog.activate(root)); await(ProjectStartDialogProbe::hasDialog);
            fx(() -> ProjectStartDialog.openedExternally(root)); await(() -> !hasDialog());
            Thread.sleep(400); require(!hasDialog() && !root.isDisabled(), "opening an old project left entry blocking editing");
            System.out.println("PASS: hidden prewarm, mandatory validated naming, no empty snapshot, next activation and opened legacy project");
        } finally { prewarm.setBoolean(null, false); try { fx(() -> { if (owner != null) owner.hide(); }); } finally { Platform.exit(); } }
    }
    private static DialogPane popup() {
        return (DialogPane) List.copyOf(Window.getWindows()).stream().filter(window -> window != owner && window.isShowing()
            && window.getScene().getRoot() instanceof DialogPane).findFirst().orElseThrow().getScene().getRoot();
    }
    private static boolean hasDialog() {
        AtomicBoolean value = new AtomicBoolean(); try { fx(() -> { try { popup(); value.set(true); } catch (Exception ignored) { } }); } catch (Exception error) { throw new RuntimeException(error); } return value.get();
    }
    private static void choose(String label) { DialogPane pane = popup(); var type = pane.getButtonTypes().stream().filter(button -> button.getText().equals(label)).findFirst().orElseThrow(); ((Button) pane.lookupButton(type)).fire(); }
    private static void fx(Runnable action) throws Exception { CompletableFuture<Void> done = new CompletableFuture<>(); Platform.runLater(() -> { try { action.run(); done.complete(null); } catch (Throwable error) { done.completeExceptionally(error); } }); done.get(10, TimeUnit.SECONDS); }
    private static void await(BooleanSupplier ready) throws Exception { long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(10); while (!ready.getAsBoolean()) { if (System.nanoTime() > end) throw new AssertionError("test timed out"); Thread.sleep(20); } }
    private static void require(boolean ok, String text) { if (!ok) throw new AssertionError(text); }
}
