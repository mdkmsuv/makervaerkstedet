package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.lang.reflect.Field;
import java.nio.file.*;
import java.util.UUID;

/** Native editable projects and local IPC; no Stage and no desktop interaction. */
public final class ProjectFinishTest {
    public static void main(String[] args) throws Exception {
        Path root = Files.createTempDirectory("cadoodle-finish-test-");
        System.setProperty("elevhub.projects", root.resolve("Projekter").toString());
        CaDoodleFile.setImageEngine((db, shapes, file) -> new javafx.scene.image.WritableImage(1, 1));
        CaDoodleFile file = LearningProjectService.create(ProjectNaming.workingDirectory().toPath().resolve("source/Test.doodle").toFile(), "name-tag", "Ida");
        ActiveProject project = new ActiveProject();
        Field active = ActiveProject.class.getDeclaredField("fromFile"); active.setAccessible(true); active.set(project, file);
        try {
            require(ProjectNaming.hasUnpublishedChanges(file), "autosave must not acknowledge an explicit pupil save");
            var name = new ProjectNaming.Name("4.A", "Ida M.", "Min nøglering");
            file.setProjectName(name.projectName());
            require(project.prepareProjectClose(true), "save must complete");
            Path saved = ProjectNaming.publish(file, name);
            require(saved.getParent().getFileName().toString().equals("4.A · Ida M"), "class and pupil folder missing");
            Path snapshot = saved.resolve(file.getSelf().getName());
            byte[] original = Files.readAllBytes(snapshot);
            require(ProjectNaming.publish(file, name).equals(saved), "repeated explicit save created a duplicate project");
            require(!ProjectNaming.hasUnpublishedChanges(file), "published baseline must be clean");
            require(ProjectNaming.knownClasses().equals(java.util.List.of("4.A")), "known class suggestions missing");
            file.setProjectName("Mit nye navn");
            require(ProjectNaming.hasUnpublishedChanges(file), "later rename must remain dirty");
            project.prepareProjectClose(true);
            require(java.util.Arrays.equals(original, Files.readAllBytes(snapshot)), "autosave overwrote the explicit snapshot");
            Files.createDirectory(file.getSelf().toPath().getParent().resolve(".elevhub-project.json.tmp"));
            boolean publishFailed = false;
            try { ProjectNaming.publish(file, ProjectNaming.name(file)); }
            catch (java.io.IOException expected) { publishFailed = true; }
            require(publishFailed && java.util.Arrays.equals(original, Files.readAllBytes(snapshot)), "failed publish changed last explicit save");
            require(ProjectNaming.name(file) != null, "existing pupil metadata was lost");
            Path copied = ProjectNaming.workingCopy(snapshot.toFile()).toPath();
            require(copied.startsWith(ProjectNaming.workingDirectory().toPath()), "saved file must open as private copy");
            Files.writeString(copied, "working edit");
            require(java.util.Arrays.equals(original, Files.readAllBytes(snapshot)), "working copy changed saved file");
            require(project.prepareProjectClose(false), "discard preparation failed");
            project.resetProjectAfterFinish();
            project.cancelClosePreparation();
            require(!Files.exists(file.getSelf().toPath().getParent()), "finished private working copy was not removed");
            try (var siblings = Files.list(saved.getParent())) {
                require(siblings.noneMatch(path -> path.getFileName().toString().startsWith(".")), "save left private staging or backups behind");
            }
            require(project.get() != file && project.get().getOperations().isEmpty(), "finish did not create an empty independent history");
            require(!project.get().isBackAvailable() && !project.get().isForwardAvailable(), "old history can be restored after finish");
            require(!project.hasUnsavedChanges() && !ProjectNaming.hasUnpublishedChanges(project.get()), "new project baseline was not clean");
            require(java.util.Arrays.equals(original, Files.readAllBytes(snapshot)), "discard changed saved project");
            CaDoodleFile restored = CaDoodleFile.fromFile(snapshot.toFile());
            try {
                Thread regenerated = restored.regenerateAll(); if (regenerated != null) regenerated.join();
                require(!restored.getCurrentState().isEmpty(), "saved snapshot lost its model after working cleanup");
                require(!ProjectNaming.hasUnpublishedChanges(restored), "reopening a saved snapshot changed the saved baseline");
            } finally { restored.close(); }
            checkProtocol(root.resolve("inbox"));
            for (String bad : new String[] {"", "../", "a/b", "a\\b", "a\nB"}) {
                boolean rejected = false;
                try { new ProjectNaming.Name(bad, "Ida", "Projekt"); }
                catch (IllegalArgumentException expected) { rejected = true; }
                require(rejected, "invalid naming accepted: " + bad);
            }
            System.out.println("PASS: explicit save, class/pupil names, autosave separation, working copies, discard, blank history/baseline, relocated snapshot reopen and IPC validation");
        } finally {
            Field open = ActiveProject.class.getDeclaredField("isOpenValue"); open.setAccessible(true); open.set(project, false);
            Field worker = ActiveProject.class.getDeclaredField("autosaveThread"); worker.setAccessible(true);
            Thread thread = (Thread) worker.get(project); if (thread != null) { thread.interrupt(); thread.join(2000); }
            project.get().close();
        }
    }
    private static void checkProtocol(Path root) throws Exception {
        Files.createDirectories(root);
        String id = UUID.randomUUID().toString().replace("-", "");
        Path request = root.resolve(id + ".ready");
        Files.writeString(request, "{\"id\":\"" + id + "\",\"action\":\"finish\",\"created\":" + System.currentTimeMillis()/1000.0 + "}");
        ProjectFinishInbox.validate(request, id);
        ProjectFinishInbox.writeStatus(root, id, "completed", "Klar");
        require(Files.readString(root.resolve(id + ".status.json")).contains("completed"), "completion receipt missing");
        Files.writeString(request, "{\"id\":\"" + id + "\",\"action\":\"finish\",\"created\":0}");
        boolean rejected = false;
        try { ProjectFinishInbox.validate(request, id); } catch (java.io.IOException expected) { rejected = true; }
        require(rejected, "expired request was accepted");
        Files.writeString(request, "{\"id\":\"" + id + "\",\"action\":\"discard\",\"created\":" + System.currentTimeMillis()/1000.0 + "}");
        rejected = false;
        try { ProjectFinishInbox.validate(request, id); } catch (java.io.IOException expected) { rejected = true; }
        require(rejected, "IPC must not accept a discard decision");
    }
    private static void require(boolean condition, String message) { if (!condition) throw new AssertionError(message); }
}
