package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.neuronrobotics.bowlerstudio.scripting.cadoodle.CaDoodleFile;
import java.lang.reflect.Field;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import javafx.application.Platform;
import javafx.scene.Node;
import javafx.scene.Parent;
import javafx.scene.Scene;
import javafx.scene.control.Button;
import javafx.scene.control.ScrollPane;
import javafx.scene.control.TitledPane;
import javafx.scene.control.ComboBox;
import javafx.scene.layout.Pane;
import sun.misc.Unsafe;

/** Help is a reversible 2D panel; rendering or revealing it must never edit a project. No Stage. */
public final class LearningLibraryProbe {
    public static void main(String[] args) throws Exception {
        Platform.startup(() -> { });
        try {
            Field unsafeField = Unsafe.class.getDeclaredField("theUnsafe"); unsafeField.setAccessible(true);
            ActiveProject active = (ActiveProject) ((Unsafe) unsafeField.get(null)).allocateInstance(ActiveProject.class);
            CaDoodleFile file = new CaDoodleFile();
            Field projectField = ActiveProject.class.getDeclaredField("fromFile"); projectField.setAccessible(true);
            projectField.set(active, file);
            onFx(() -> {
                Pane root = new Pane(); Scene scene = new Scene(root, 1280, 720);
                scene.getStylesheets().add(LearningLibrary.class.getResource("/com/commonwealthrobotics/stylesheet.css").toExternalForm());
                root.resize(1280, 720);
                Button group = new Button("Gruppér"); group.setId("groupButton"); group.setDisable(true);
                group.setOnAction(event -> { throw new AssertionError("Help invoked a modelling action"); });
                root.getChildren().add(group);
                LearningLibrary library = new LearningLibrary(active, root);
                root.getChildren().addAll(library.button(), library.guidance());
                var help = LearningLibrary.class.getDeclaredMethod("showGuidance", LearningCatalog.Entry.class);
                help.setAccessible(true);
                Path folder = Files.createTempDirectory("learning-guide-navigation-");
                for (var entry : LearningCatalog.entries()) {
                    Path model = folder.resolve(entry.id() + ".doodle");
                    Files.writeString(model, "model fixture remains unchanged"); file.setSelf(model.toFile());
                    help.invoke(library, entry);
                    TitledPane guide = (TitledPane) library.guidance();
                    require(guide.isVisible() && guide.isExpanded(), "project help is inaccessible");
                    Button free = button(guide, "Arbejd frit");
                    require(free != null, "missing free-work choice"); free.fire();
                    require(!guide.isExpanded(), "free-work did not collapse help");
                    guide.setExpanded(true);
                    var course = LearningCourses.steps(entry.id());
                    require(course.size() >= 6 && course.size() <= 10, "missing usable sequence");
                    Button previous = (Button) find(guide, "learning-step-back");
                    require(previous.isDisabled(), "first step permits negative index");
                    ComboBox<?> picker = (ComboBox<?>) find(guide, "learning-step-picker");
                    picker.getSelectionModel().select(2); // Skipping is allowed, including before modelling.
                    require(LearningProgress.read(model, entry.id(), course.size()).step() == 2, "step bookmark missing");
                    previous.fire();
                    require(LearningProgress.read(model, entry.id(), course.size()).step() == 1, "back did not save previous step");
                    picker.getSelectionModel().select(2);
                    help.invoke(library, entry);
                    picker = (ComboBox<?>) find(guide, "learning-step-picker");
                    require(picker.getSelectionModel().getSelectedIndex() == 2, "guide did not resume");
                    picker.getSelectionModel().selectFirst();
                    for (int step = 0; step < course.size(); step++) {
                        Button tool = button(guide, "Vis værktøjet"); if (tool != null) tool.fire();
                        require(group.isDisabled(), "help enabled a native tool");
                        require(active.get() == file && file.getOperations().isEmpty(), "guide edited model");
                        ((Button) find(guide, "learning-step-next")).fire();
                    }
                    require(LearningProgress.read(model, entry.id(), course.size()).completed(), "completion not saved");
                    help.invoke(library, entry);
                    require(button(guide, "Gennemgå trinnene") != null, "completion not restored");
                    button(guide, "Gennemgå trinnene").fire();
                    require(LearningProgress.read(model, entry.id(), course.size()).step() == 0
                            && !LearningProgress.read(model, entry.id(), course.size()).completed(), "restart failed");
                    require(group.isDisabled(), "help changed native tool availability");
                    require(active.get() == file && file.getOperations().isEmpty(), "help edited the active project");
                    require(Files.readString(model).equals("model fixture remains unchanged"), "guide changed project file");
                    guide.setPrefWidth(260);
                    root.applyCss();
                    for (double available : new double[]{330, 380}) {
                        guide.resize(260, available); guide.layout();
                        Node next = find(guide, "learning-step-next");
                        Node coach = guide.getContent();
                        require(next.getBoundsInParent().getMaxX() <= coach.getBoundsInLocal().getWidth() + 1,
                            "step navigation overflows the 260px sidebar");
                        require(find(guide, "learning-step-scroll").getBoundsInLocal().getHeight() > 45,
                            "guide has no usable reading area");
                    }
                }
                Path corrupt = folder.resolve("broken.doodle");
                Files.writeString(folder.resolve("broken.doodle.learning-progress.properties"), "course=robot\nversion=2\nstep=oops\n");
                require(LearningProgress.read(corrupt, "robot", 9).step() == 0, "bad bookmark was not recovered");
                require(LearningProgress.read(file.getSelf().toPath(), "different-course", 9).step() == 0, "guide progress leaked between courses");
                // Detach the owning root: its observation timeline must be stopped.
                root.getScene().setRoot(new Pane());
            });
            System.out.println("PASS: all six step guides, skip/back/finish/restart/resume, corrupt bookmarks, free work and unchanged models/tools");
        } finally { Platform.exit(); }
    }

    private static Node find(Node node, String id) {
        if (id.equals(node.getId())) return node;
        if (node instanceof TitledPane pane) return find(pane.getContent(), id);
        if (node instanceof ScrollPane scroll) return find(scroll.getContent(), id);
        if (node instanceof Parent parent) for (Node child : parent.getChildrenUnmodifiable()) {
            Node found = find(child, id); if (found != null) return found;
        }
        return null;
    }

    private static Button button(Node node, String prefix) {
        if (node instanceof Button button && button.getText().startsWith(prefix)) return button;
        if (node instanceof TitledPane pane) return button(pane.getContent(), prefix);
        if (node instanceof ScrollPane scroll) return button(scroll.getContent(), prefix);
        if (node instanceof Parent parent) for (Node child : parent.getChildrenUnmodifiable()) {
            Button found = button(child, prefix); if (found != null) return found;
        }
        return null;
    }
    private interface Checked { void run() throws Exception; }
    private static void onFx(Checked runnable) throws Exception {
        CountDownLatch done = new CountDownLatch(1); AtomicReference<Throwable> error = new AtomicReference<>();
        Platform.runLater(() -> { try { runnable.run(); } catch (Throwable failure) { error.set(failure); } finally { done.countDown(); } });
        if (!done.await(15, TimeUnit.SECONDS)) throw new AssertionError("FX timed out");
        if (error.get() != null) throw new AssertionError(error.get());
    }
    private static void require(boolean value, String message) { if (!value) throw new AssertionError(message); }
}
