import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.kloster.*;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import javafx.application.Platform;
import javafx.scene.Scene;
import javafx.scene.control.*;
import javafx.scene.layout.Pane;
import javafx.stage.*;

/** File-inbox replacement must wait for a pupil decision, even with an older launcher. */
public final class ProjectInboxFinishProbe {
    private static final class Blank extends CaDoodleFile { @Override public boolean isInitialized() { return true; } }
    private static final class Project extends ActiveProject {
        volatile CaDoodleFile current;
        volatile int imports, resets;
        volatile boolean dirty = true;
        Project() throws Exception { current = file("old"); }
        private CaDoodleFile file(String name) throws Exception {
            Path folder = ProjectNaming.workingDirectory().toPath().resolve(name); Files.createDirectories(folder);
            CaDoodleFile file = new Blank().setSelf(folder.resolve("Test.doodle").toFile()).setProjectName(name);
            Files.writeString(file.getSelf().toPath(), file.toJson()); return file;
        }
        @Override public CaDoodleFile get() { return current; }
        @Override public boolean hasUnsavedChanges() { return dirty; }
        @Override public boolean prepareProjectClose(boolean save) { if (save) throw new AssertionError("unexpected save"); return true; }
        @Override public void resetProjectAfterFinish() throws Exception { current = file("blank-" + (++resets)); dirty = false; }
        @Override public void loadFromZip(java.io.File zip) { try { current = file("opened-" + (++imports)); } catch (Exception failure) { throw new RuntimeException(failure); } }
    }
    private static Stage stage;
    private static Pane root;
    private static Project project;
    public static void main(String[] args) throws Exception {
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        Platform.startup(() -> Platform.setImplicitExit(false));
        try {
            fx(() -> { try { project = new Project(); root = new Pane(); stage = new Stage(); ProjectCloseGuard.install(root, project);
                stage.setScene(new Scene(root, 650, 450)); stage.show(); new ProjectInbox(project, null, root);
            } catch (Exception failure) { throw new RuntimeException(failure); } });
            Path first = request(); awaitDialog(); fx(() -> choose("Arbejd videre")); awaitStatus(first, "error");
            require(project.imports == 0 && project.resets == 0, "cancelled inbox request replaced the active model");
            Path second = request(); awaitDialog(); require(project.imports == 0, "import happened before decision");
            fx(() -> choose("Afslut uden at gemme")); awaitStatus(second, "done");
            require(project.imports == 1 && project.resets == 1, "completed request did not open exactly once after reset");
            System.out.println("PASS: project-inbox cancellation blocks loading; accepted finish opens once after reset");
        } finally { try { fx(() -> { if (stage != null) stage.hide(); }); } finally { Platform.exit(); } }
    }
    private static Path request() throws Exception {
        Path folder = ProjectNaming.projectsRoot().resolve(".elevhub/open-requests"); Files.createDirectories(folder);
        String id = UUID.randomUUID().toString().replace("-", ""); byte[] contents = "isolated loader fixture".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        Files.write(folder.resolve(id + ".zip"), contents);
        String digest = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(contents));
        Files.writeString(folder.resolve(id + ".ready"), "{\"id\":\"" + id + "\",\"file\":\"" + id + ".zip\",\"created\":"
            + System.currentTimeMillis()/1000.0 + ",\"sha256\":\"" + digest + "\",\"name\":\"Gemt projekt\"}");
        return folder.resolve(id + ".status.json");
    }
    private static void awaitDialog() throws Exception {
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (true) {
            var found = new java.util.concurrent.atomic.AtomicBoolean(); fx(() -> found.set(List.copyOf(Window.getWindows()).stream().anyMatch(window -> window != stage && window.isShowing())));
            if (found.get()) return; if (System.nanoTime() > until) throw new AssertionError("inbox did not ask before opening"); Thread.sleep(20);
        }
    }
    private static void awaitStatus(Path file, String status) throws Exception {
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
        while (!Files.exists(file)) { if (System.nanoTime() > until) throw new AssertionError("inbox status missing"); Thread.sleep(20); }
        require(Files.readString(file).contains("\"state\":\"" + status + "\""), "unexpected inbox status: " + Files.readString(file));
    }
    private static void choose(String label) {
        for (Window window : List.copyOf(Window.getWindows())) if (window.isShowing() && window.getScene().getRoot() instanceof DialogPane pane)
            for (ButtonType type : pane.getButtonTypes()) if (type.getText().equals(label)) { ((Button) pane.lookupButton(type)).fire(); return; }
        throw new AssertionError("missing choice " + label);
    }
    private static void fx(Runnable action) throws Exception { CompletableFuture<Void> done = new CompletableFuture<>(); Platform.runLater(() -> { try { action.run(); done.complete(null); } catch (Throwable error) { done.completeExceptionally(error); } }); done.get(10, TimeUnit.SECONDS); }
    private static void require(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
}
