import eu.mihosoft.vrl.v3d.CSG;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicReference;
import javafx.application.Platform;
import javafx.scene.shape.MeshView;
import javafx.scene.shape.TriangleMesh;
import javafx.scene.text.Font;

/** Generates portable TriangleMesh label assets from the verified Windows font. */
public final class GenerateWorkplaneLabels {
    private static final int MAGIC = 0x43574631;

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException(
                "usage: GenerateWorkplaneLabels <font.ttf> <output-dir>");
        Path fontPath = Path.of(args[0]).toAbsolutePath();
        Path output = Path.of(args[1]).toAbsolutePath();
        Files.createDirectories(output);
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        Platform.startup(() -> {
            try {
                Font font;
                try (var fontStream = Files.newInputStream(fontPath)) {
                    font = Font.loadFont(fontStream, 11);
                }
                if (font == null) throw new IOException("Unable to load " + fontPath);
                write(output.resolve("workplane.mesh"), "Arbejdsplan", 60, 9, font);
                write(output.resolve("millimetres.mesh"), "Millimeter", 35, 7, font);
            } catch (Throwable error) {
                failure.set(error);
            } finally {
                done.countDown();
            }
        });
        done.await();
        Platform.exit();
        if (failure.get() != null) throw new RuntimeException(failure.get());
        System.out.println("Generated workplane-label meshes in " + output);
    }

    private static void write(Path file, String label, double width, double height,
            Font font) throws IOException {
        MeshView view = CSG.textToSize(label, width, height, 0.1, font)
                .moveToCenter().toZMin().newMesh();
        if (!(view.getMesh() instanceof TriangleMesh mesh))
            throw new IOException("CSG did not produce TriangleMesh for " + label);
        try (OutputStream stream = Files.newOutputStream(file);
                DataOutputStream data = new DataOutputStream(stream)) {
            data.writeInt(MAGIC);
            data.writeUTF(label);
            writeFloats(data, mesh.getPoints());
            writeFloats(data, mesh.getTexCoords());
            writeInts(data, mesh.getFaces());
        }
        System.out.printf("%s: points=%d texCoords=%d faces=%d bounds=%s%n", label,
                mesh.getPoints().size(), mesh.getTexCoords().size(), mesh.getFaces().size(),
                view.getBoundsInParent());
    }

    private static void writeFloats(DataOutputStream data,
            javafx.collections.ObservableFloatArray values) throws IOException {
        float[] items = new float[values.size()];
        values.toArray(items);
        data.writeInt(items.length);
        for (float item : items) data.writeFloat(item);
    }

    private static void writeInts(DataOutputStream data,
            javafx.collections.ObservableIntegerArray values) throws IOException {
        int[] items = new int[values.size()];
        values.toArray(items);
        data.writeInt(items.length);
        for (int item : items) data.writeInt(item);
    }
}
