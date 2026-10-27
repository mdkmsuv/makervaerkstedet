import com.commonwealthrobotics.kloster.WorkshopThumbnails;
import eu.mihosoft.vrl.v3d.CSG;
import eu.mihosoft.vrl.v3d.Cube;
import javafx.scene.image.WritableImage;

import java.io.File;
import java.awt.image.BufferedImage;
import java.nio.file.Files;
import javax.imageio.ImageIO;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/** Concurrent, offscreen regression for the per-request thumbnail provider. */
public final class WorkshopThumbnailsTest {
    public static void main(String[] args) throws Exception {
        File destination = Files.createTempDirectory("cadoodle-thumbnail-test").toFile();
        // Keep this probe toolkit-free: ThumbnailImage's file fast path still
        // exercises the exact callback used by CaDoodleFile from worker threads.
        File cached = new File(destination, "cached.png");
        ImageIO.write(new BufferedImage(2, 2, BufferedImage.TYPE_INT_ARGB), "png", cached);
        ConcurrentLinkedQueue<Throwable> uncaught = new ConcurrentLinkedQueue<>();
        Thread.setDefaultUncaughtExceptionHandler((thread, failure) -> uncaught.add(failure));
        var pool = Executors.newFixedThreadPool(8);
        try {
            List<Callable<WritableImage>> calls = new ArrayList<>();
            for (int i = 0; i < 8; i++) {
                final int index = i;
                calls.add(() -> {
                    CSG cube = new Cube(12 + index, 10, 4).toCSG();
                    cube.setName("thumbnail-part-" + index);
                    return WorkshopThumbnails.get(null, List.of(cube), cached);
                });
            }
            List<Future<WritableImage>> results = pool.invokeAll(calls, 120, TimeUnit.SECONDS);
            for (Future<WritableImage> result : results) {
                require(!result.isCancelled(), "thumbnail request timed out");
                require(result.get(1, TimeUnit.SECONDS) != null, "thumbnail request returned null");
            }
            require(uncaught.isEmpty(), "uncaught thumbnail failure: " + uncaught.peek());
            System.out.println("PASS: concurrent thumbnail requests use independent providers");
        } finally {
            pool.shutdownNow();
            delete(destination);
        }
    }

    private static void delete(File file) {
        if (file.isDirectory()) {
            File[] children = file.listFiles();
            if (children != null) for (File child : children) delete(child);
        }
        file.delete();
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
