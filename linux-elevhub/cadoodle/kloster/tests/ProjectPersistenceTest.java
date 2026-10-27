import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.kloster.LearningProjectService;
import com.commonwealthrobotics.kloster.ProjectPersistence;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.lang.reflect.Field;
import java.nio.file.Files;
import java.nio.file.Path;

/** Real native project files: missing files, unsignalled edits, failed writes and retry. */
public final class ProjectPersistenceTest {
    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("cadoodle-save-close-");
        Path path = root.resolve("Project.doodle");
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        CaDoodleFile file = LearningProjectService.create(path.toFile(), "name-tag", "Gemmetest");
        ActiveProject project = new ActiveProject();
        Field active = ActiveProject.class.getDeclaredField("fromFile");
        active.setAccessible(true);
        active.set(project, file);
        try {
            require(ProjectPersistence.matchesDisk(file), "a saved native file must be clean");
            require(!project.hasUnsavedChanges(), "clean native project unexpectedly dirty");
            String stable = file.toJson();
            require(stable.equals(file.toJson()), "serializing must not change the project");
            file.setProjectName("Mit ændrede projekt");
            require(project.hasUnsavedChanges(), "name change without autosave request must be detected");
            project.pauseAutomaticSavingForClose();
            project.save();
            require(project.hasUnsavedChanges(), "queued save must remain dirty before it writes");
            require(project.prepareProjectClose(true), "save-and-close did not complete");
            require(!project.hasUnsavedChanges(), "verified native save must become clean");
            CaDoodleFile reopened = CaDoodleFile.fromFile(path.toFile());
            require("Mit ændrede projekt".equals(reopened.getMyProjectName()), "saved name was not persisted");
            reopened.close();

            file.setProjectName("Bevar mig ved fejl");
            Path directory = Files.createDirectory(root.resolve("not-a-project-file"));
            file.setSelf(directory.toFile());
            project.save();
            boolean failed = false;
            try { project.prepareProjectClose(true); }
            catch (Exception expected) { failed = true; }
            require(failed, "writing to a directory must fail");
            require(project.hasUnsavedChanges(), "failed write must leave the project dirty");
            file.setSelf(path.toFile());
            require(project.prepareProjectClose(true), "retry after fixing destination must succeed");
            require(!project.hasUnsavedChanges(), "successful retry did not clear failed save");

            Files.writeString(path, "{ incomplete");
            require(project.hasUnsavedChanges(), "a truncated file must never count as saved");
            require(project.prepareProjectClose(true), "repairing truncated project failed");
            Files.delete(path);
            require(project.hasUnsavedChanges(), "missing project must be dirty");
            require(project.prepareProjectClose(false), "discard should wait safely without saving");
            require(!Files.exists(path), "discard unexpectedly saved the project");
            project.cancelClosePreparation();
            System.out.println("PASS: native project save/close, rename, missing/truncated file, failed save and retry");
        } finally {
            Field open = ActiveProject.class.getDeclaredField("isOpenValue");
            open.setAccessible(true);
            open.set(project, false);
            Field worker = ActiveProject.class.getDeclaredField("autosaveThread");
            worker.setAccessible(true);
            Thread thread = (Thread) worker.get(project);
            if (thread != null) { thread.interrupt(); thread.join(2000); }
            file.close();
        }
    }

    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }
}
