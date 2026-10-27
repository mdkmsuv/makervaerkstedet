import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.kloster.ProjectCloseGuard;
import com.commonwealthrobotics.kloster.ProjectNaming;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.nio.file.*;
import java.util.List;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;
import java.util.function.BooleanSupplier;
import javafx.application.Platform;
import javafx.event.Event;
import javafx.scene.Scene;
import javafx.scene.control.*;
import javafx.scene.layout.Pane;
import javafx.stage.*;

/** Private Xvfb only: the real JavaFX guard plus an explicitly controlled save boundary. */
public final class ProjectCloseGuardProbe {
    private static final class Project extends ActiveProject {
        volatile boolean dirty = true, paused, fail;
        volatile int saves, resets;
        volatile CountDownLatch saveGate;
        CaDoodleFile file;
        Project(boolean named) throws Exception {
            Path folder = Files.createTempDirectory("close-guard-fixture-");
            file = CaDoodleFile.fromJsonString("{}", null, folder.resolve("Test.doodle").toFile(), false);
            if (named) ProjectNaming.nameDraft(file, new ProjectNaming.Name("4.A", "Ida", "Min figur"));
        }
        @Override public CaDoodleFile get() { return file; }
        @Override public boolean hasUnsavedChanges() { return dirty; }
        @Override public void pauseAutomaticSavingForClose() { paused = true; }
        @Override public void cancelClosePreparation() { paused = false; }
        @Override public boolean prepareProjectClose(boolean save) throws Exception {
            paused = true;
            if (save) {
                saves++;
                if (saveGate != null && !saveGate.await(5, TimeUnit.SECONDS)) throw new AssertionError("save gate timed out");
                if (fail) throw new java.io.IOException("test write failure");
                Files.writeString(file.getSelf().toPath(), file.toJson()); dirty = false;
            }
            return true;
        }
        @Override public void resetProjectAfterFinish() { resets++; dirty = false; }
    }
    private static final class Case {
        final Project project;
        final Stage owner = new Stage();
        final Pane root = new Pane();
        final AtomicBoolean closed = new AtomicBoolean();
        final AtomicReference<String> result = new AtomicReference<>();
        Case(boolean dirty) throws Exception { this(dirty, true); }
        Case(boolean dirty, boolean named) throws Exception {
            project = new Project(named); project.dirty = dirty;
            ProjectCloseGuard.install(root, project); owner.setScene(new Scene(root, 600, 400));
            owner.setOnCloseRequest(event -> { closed.set(true); owner.hide(); event.consume(); }); owner.show();
        }
        void request() { ProjectCloseGuard.requestFinish(root, result::set); }
        void closeRequest() { Event.fireEvent(owner, new WindowEvent(owner, WindowEvent.WINDOW_CLOSE_REQUEST)); }
        Window popup() {
            return List.copyOf(Window.getWindows()).stream().filter(window -> window != owner && window.isShowing()
                && window.getScene().getRoot() instanceof DialogPane).findFirst().orElseThrow(() -> new AssertionError("dialog missing"));
        }
        void choose(String label) {
            DialogPane pane = (DialogPane) popup().getScene().getRoot();
            var type = pane.getButtonTypes().stream().filter(item -> item.getText().equals(label)).findFirst().orElseThrow();
            ((Button) pane.lookupButton(type)).fire();
        }
    }
    public static void main(String[] args) throws Exception {
        System.setProperty("elevhub.projects", Files.createTempDirectory("close-guard-projects-").toString());
        Platform.startup(() -> Platform.setImplicitExit(false));
        try {
            Case cancel = create(true); fx(cancel::request);
            fx(() -> cancel.choose("Arbejd videre"));
            require("cancelled".equals(cancel.result.get()) && cancel.project.dirty && !cancel.project.paused && !cancel.root.isDisabled(), "cancel lost work or kept input paused");
            fx(cancel.owner::hide);

            Case cross = create(true); fx(cross::request);
            fx(() -> Event.fireEvent(cross.popup(), new WindowEvent(cross.popup(), WindowEvent.WINDOW_CLOSE_REQUEST)));
            require("cancelled".equals(cross.result.get()), "popup close must cancel"); fx(cross.owner::hide);

            Case save = create(true); save.project.saveGate = new CountDownLatch(1); fx(save::request);
            AtomicReference<String> duplicate = new AtomicReference<>(); fx(() -> ProjectCloseGuard.requestFinish(save.root, duplicate::set));
            require("error".equals(duplicate.get()), "duplicate request must not replace active callback");
            fx(() -> save.choose("Gem og afslut")); await(() -> save.project.saves == 1);
            require(save.result.get() == null && save.project.resets == 0, "completed before pending save");
            fx(() -> Event.fireEvent(save.popup(), new WindowEvent(save.popup(), WindowEvent.WINDOW_CLOSE_REQUEST)));
            fx(() -> require(save.popup().isShowing(), "save popup closed while write active"));
            save.project.saveGate.countDown(); await(() -> save.project.resets == 1);
            await(() -> "completed".equals(save.result.get()));
            fx(() -> require(List.copyOf(Window.getWindows()).stream().noneMatch(window -> window != save.owner && window.isShowing()), "save-and-finish requested a second confirmation"));
            fx(save.owner::hide);

            Case failure = create(true); failure.project.fail = true; fx(failure::request);
            fx(() -> failure.choose("Gem og afslut")); await(() -> popupHeader(failure).startsWith("Projektet er stadig åbent."));
            require(failure.result.get() == null && failure.project.dirty && failure.project.resets == 0, "failed save finished project");
            fx(() -> failure.choose("Arbejd videre")); fx(failure.owner::hide);

            Case discard = create(true); fx(discard::closeRequest); fx(() -> discard.choose("Afslut uden at gemme"));
            await(discard.closed::get); require(discard.project.saves == 0 && discard.project.resets == 1, "discard saved or failed to reset");

            Case clean = create(false); fx(clean::request); await(() -> "completed".equals(clean.result.get()));
            require(clean.project.saves == 0 && clean.project.resets == 1, "clean finish must reset without save"); fx(clean.owner::hide);

            Case explicit = create(true); fx(() -> ProjectCloseGuard.requestSave(explicit.root));
            await(() -> popupHeader(explicit).startsWith("Gemt:"));
            require(explicit.project.saves == 1 && explicit.project.resets == 0, "Gem projekt unexpectedly reset the model");
            fx(() -> explicit.choose("Arbejd videre")); require(!explicit.project.paused, "save confirmation did not resume editing"); fx(explicit.owner::hide);
            AtomicReference<Case> legacyFixture = new AtomicReference<>();
            fx(() -> { try { legacyFixture.set(new Case(true, false)); } catch (Exception error) { throw new RuntimeException(error); } });
            Case legacy = legacyFixture.get(); fx(() -> ProjectCloseGuard.requestSave(legacy.root));
            fx(() -> {
                DialogPane pane = (DialogPane) legacy.popup().getScene().getRoot();
                var box = (javafx.scene.layout.VBox) pane.getContent(); var form = (javafx.scene.layout.GridPane) box.getChildren().get(1);
                ComboBox<?> classes = (ComboBox<?>) form.getChildren().stream().filter(node -> node instanceof ComboBox).findFirst().orElseThrow();
                var text = form.getChildren().stream().filter(node -> node instanceof TextField).map(node -> (TextField) node).toList();
                classes.getEditor().setText("4.A"); text.get(0).setText("Ida"); text.get(1).setText("Første navn"); legacy.choose("Gem projekt");
            });
            await(() -> popupHeader(legacy).equals("Gemt: Første navn")); fx(() -> legacy.choose("Arbejd videre"));
            fx(() -> { legacy.project.file.setProjectName("Nyt navn"); ProjectCloseGuard.requestSave(legacy.root); });
            await(() -> popupHeader(legacy).equals("Gemt: Nyt navn"));
            require(legacy.project.saves == 2 && legacy.project.file.getMyProjectName().equals("Nyt navn"), "later named save reused stale form values");
            fx(() -> { legacy.choose("Arbejd videre"); legacy.owner.hide(); });
            System.out.println("PASS: async cancel/X, duplicate request, gated save, one-click finish, failed save, discard/clean reset, explicit save and repeated legacy naming");
        } finally { Platform.exit(); }
    }
    private static String popupHeader(Case test) {
        AtomicReference<String> header = new AtomicReference<>("");
        try { fx(() -> header.set(((DialogPane) test.popup().getScene().getRoot()).getHeaderText())); }
        catch (Exception ignored) { }
        return header.get() == null ? "" : header.get();
    }
    private static Case create(boolean dirty) throws Exception {
        AtomicReference<Case> result = new AtomicReference<>(); fx(() -> { try { result.set(new Case(dirty)); } catch (Exception error) { throw new RuntimeException(error); } }); return result.get();
    }
    private static void fx(Runnable action) throws Exception {
        CompletableFuture<Void> done = new CompletableFuture<>(); Platform.runLater(() -> { try { action.run(); done.complete(null); } catch (Throwable error) { done.completeExceptionally(error); } }); done.get(10, TimeUnit.SECONDS);
    }
    private static void await(BooleanSupplier ready) throws Exception {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
        while (!ready.getAsBoolean()) { if (System.nanoTime() > deadline) throw new AssertionError("test timed out"); Thread.sleep(10); }
    }
    private static void require(boolean value, String message) { if (!value) throw new AssertionError(message); }
}
