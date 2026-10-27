import com.sun.tools.attach.VirtualMachine;
import javafx.application.Platform;
import javafx.embed.swing.SwingFXUtils;
import javafx.scene.Node;
import javafx.scene.Parent;
import javafx.scene.Scene;
import javafx.scene.SnapshotParameters;
import javafx.scene.control.Labeled;
import javafx.scene.control.TextInputControl;
import javafx.scene.image.WritableImage;
import javafx.scene.text.Text;
import javafx.stage.Window;

import javax.imageio.ImageIO;
import java.io.IOException;
import java.lang.instrument.Instrumentation;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Read-only JavaFX capture and geometry diagnostics for a running workshop. */
public final class WorkshopCaptureAgent {
    private WorkshopCaptureAgent() { }

    /** Agent entry point. The argument is the directory receiving PNGs and diagnostics. */
    public static void agentmain(String outputDir, Instrumentation instrumentation) {
        Path destination = Paths.get(outputDir == null || outputDir.isBlank() ? "capture" : outputDir);
        Runnable capture = () -> captureShowingWindows(destination);
        try {
            Platform.runLater(capture);
        } catch (IllegalStateException notStarted) {
            // Useful for a JavaFX-capable diagnostic JVM, while an attached app normally
            // takes the runLater path above because its toolkit is already initialized.
            Platform.startup(capture);
        }
    }

    /** Attaches the agent; this performs no UI interaction and captures only showing windows. */
    public static void main(String[] args) throws Exception {
        if (args.length != 3) {
            throw new IllegalArgumentException("Usage: WorkshopCaptureAgent <pid> <agent-jar> <output-dir>");
        }
        VirtualMachine vm = VirtualMachine.attach(args[0]);
        try {
            vm.loadAgent(args[1], args[2]);
        } finally {
            vm.detach();
        }
    }

    private static void captureShowingWindows(Path destination) {
        try {
            Files.createDirectories(destination);
            List<Window> windows = Window.getWindows().stream()
                    .filter(Window::isShowing)
                    .toList();
            List<String> report = new ArrayList<>();
            int index = 1;
            for (Window window : windows) {
                Scene scene = window.getScene();
                if (scene == null || scene.getRoot() == null) continue;
                String stem = String.format(Locale.ROOT, "window-%02d", index++);
                Path image = destination.resolve(stem + ".png");
                Path root = destination.resolve(stem + ".txt");
                try {
                    WritableImage snapshot = scene.getRoot().snapshot(new SnapshotParameters(), null);
                    ImageIO.write(SwingFXUtils.fromFXImage(snapshot, null), "png", image.toFile());
                } catch (IOException | RuntimeException captureFailure) {
                    report.add(stem + ".snapshotError=" + compact(captureFailure));
                }
                StringBuilder geometry = new StringBuilder();
                geometry.append(stem).append(".window=").append(window.getClass().getName()).append('\n');
                geometry.append(stem).append(".rootSize=")
                        .append(format(scene.getRoot().getBoundsInLocal().getWidth())).append('x')
                        .append(format(scene.getRoot().getBoundsInLocal().getHeight())).append('\n');
                for (String style : List.of("kloster-header", "kloster-actions", "kloster-shapes-panel")) {
                    List<Node> matches = findStyle(scene.getRoot(), style);
                    geometry.append(stem).append(".").append(style).append("=")
                            .append(matches.size()).append(' ');
                    for (Node match : matches) {
                        var bounds = match.localToScene(match.getBoundsInLocal());
                        geometry.append('[').append(format(bounds.getMinX())).append(',')
                                .append(format(bounds.getMinY())).append(' ')
                                .append(format(bounds.getWidth())).append('x')
                                .append(format(bounds.getHeight())).append(']');
                    }
                    geometry.append('\n');
                }
                geometry.append(stem).append(".text=").append(String.join(" | ", visibleText(scene.getRoot()))).append('\n');
                Files.writeString(root, geometry.toString());
                report.add(geometry.toString());
            }
            Files.writeString(destination.resolve("diagnostic.txt"), String.join("\n", report));
        } catch (IOException | RuntimeException failure) {
            try {
                Files.createDirectories(destination);
                Files.writeString(destination.resolve("diagnostic-error.txt"), compact(failure));
            } catch (IOException ignored) {
                // The agent must never alter the application or throw on diagnostic I/O.
            }
        }
    }

    private static List<Node> findStyle(Node node, String style) {
        List<Node> matches = new ArrayList<>();
        if (node.getStyleClass().contains(style)) matches.add(node);
        if (node instanceof Parent parent) {
            for (Node child : parent.getChildrenUnmodifiable()) matches.addAll(findStyle(child, style));
        }
        return matches;
    }

    private static List<String> visibleText(Node node) {
        List<String> text = new ArrayList<>();
        if (node instanceof Labeled labeled && labeled.isVisible() && labeled.getText() != null
                && !labeled.getText().isBlank()) text.add(labeled.getText().trim());
        if (node instanceof TextInputControl input && input.isVisible() && !input.getText().isBlank())
            text.add(input.getText().trim());
        if (node instanceof Text glyph && glyph.isVisible() && glyph.getText() != null
                && !glyph.getText().isBlank()) text.add(glyph.getText().trim());
        if (node instanceof Parent parent) {
            for (Node child : parent.getChildrenUnmodifiable()) text.addAll(visibleText(child));
        }
        return text;
    }

    private static String format(double value) {
        return String.format(Locale.ROOT, "%.1f", value);
    }

    private static String compact(Throwable failure) {
        String message = failure.getMessage();
        return failure.getClass().getSimpleName() + (message == null ? "" : ": " + message);
    }
}
