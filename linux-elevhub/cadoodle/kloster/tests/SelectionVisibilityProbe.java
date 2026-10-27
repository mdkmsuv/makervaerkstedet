import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import com.commonwealthrobotics.controls.ControlSprites;
import com.commonwealthrobotics.controls.SelectionSession;

import javafx.application.Platform;
import javafx.scene.Node;
import javafx.scene.shape.MeshView;

import sun.misc.Unsafe;

/** No-Stage regression probe for a late camera callback after deselection. */
public final class SelectionVisibilityProbe {
    private static final Unsafe UNSAFE = unsafe();

    public static void main(String[] args) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        Platform.startup(() -> {
            try {
                SelectionSession session = allocate(SelectionSession.class);
                set(session, "selected", new LinkedHashSet<>());
                ControlSprites controls = allocate(ControlSprites.class);
                set(controls, "session", session);
                MeshView staleHandle = new MeshView();
                staleHandle.setVisible(true);
                set(controls, "allElems", new ArrayList<Node>(List.of(staleHandle)));

                Method update = ControlSprites.class.getMethod("updateControls",
                        double.class, double.class, double.class, double.class, double.class,
                        double.class, double.class, double.class, List.class,
                        eu.mihosoft.vrl.v3d.Bounds.class,
                        java.util.HashMap.class, double.class);
                update.invoke(controls, 800d, 600d, -330d, 0d, 0d, 0d, 0d, 0d,
                        List.of("stale-selection"), null, new java.util.HashMap<>(), 45d);
                Platform.runLater(() -> {
                    if (staleHandle.isVisible())
                        throw new AssertionError("late empty-selection callback left a handle visible");
                    done.countDown();
                });
            } catch (Throwable error) {
                error.printStackTrace();
            }
        });
        if (!done.await(10, TimeUnit.SECONDS))
            throw new AssertionError("selection visibility probe timed out");
        Platform.exit();
        System.out.println("PASS: stale non-empty camera callback is ignored after selection clears");
    }

    private static Unsafe unsafe() {
        try {
            Field field = Unsafe.class.getDeclaredField("theUnsafe");
            field.setAccessible(true);
            return (Unsafe) field.get(null);
        } catch (Exception error) {
            throw new ExceptionInInitializerError(error);
        }
    }

    @SuppressWarnings("unchecked")
    private static <T> T allocate(Class<T> type) throws Exception {
        return (T) UNSAFE.allocateInstance(type);
    }

    private static void set(Object target, String name, Object value) throws Exception {
        Field field = target.getClass().getDeclaredField(name);
        field.setAccessible(true);
        field.set(target, value);
    }
}
