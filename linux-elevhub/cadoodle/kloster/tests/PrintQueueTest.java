import com.commonwealthrobotics.kloster.PrintModel;
import com.commonwealthrobotics.kloster.PrintQueueClient;
import com.sun.net.httpserver.HttpServer;
import eu.mihosoft.vrl.v3d.Cube;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.util.*;
import java.util.concurrent.atomic.*;

/** Exercise actual HTTP requests, a lost receipt, defaults and multi-part STL. */
public final class PrintQueueTest {
    public static void main(String[] args) throws Exception {
        Path folder = Files.createTempDirectory("kloster-print-test");
        Path stl = folder.resolve("model.stl");
        var box = new Cube(20).toCSG();
        var second = new Cube(10).toCSG().movex(40);
        var hole = new Cube(80).toCSG().setIsHole(true);
        var hidden = new Cube(100).toCSG().setIsHide(true);
        PrintModel.write(stl, List.of(box, second, hole, hidden));
        String model = Files.readString(stl);
        var points = java.util.regex.Pattern.compile("vertex\\s+([-+.0-9Ee]+)\\s+([-+.0-9Ee]+)\\s+([-+.0-9Ee]+)").matcher(model);
        int count = 0;
        double low = Double.POSITIVE_INFINITY, high = Double.NEGATIVE_INFINITY;
        while (points.find()) {
            double x = Double.parseDouble(points.group(1));
            low = Math.min(low, x); high = Math.max(high, x); count++;
        }
        check(count == 72 && Math.abs(high-low-55) < .001, "both solids retain positions; hole omitted");
        try { PrintModel.write(folder.resolve("empty.stl"), List.of(hole)); throw new AssertionError("empty accepted"); }
        catch (java.io.IOException expected) { }
        Path exact = folder.resolve("exact-175.stl");
        PrintModel.write(exact, List.of(new Cube(175).toCSG()));
        check(Files.exists(exact), "175 mm model accepted");
        Path oversized = folder.resolve("oversized-176.stl");
        try {
            PrintModel.write(oversized, List.of(new Cube(176).toCSG()));
            throw new AssertionError("176 mm model accepted");
        } catch (java.io.IOException expected) {
            check(!Files.exists(oversized), "rejected 176 mm model leaves no partial STL");
        }
        Path combined = folder.resolve("combined-176.stl");
        try {
            PrintModel.write(combined, List.of(new Cube(100).toCSG(), new Cube(100).toCSG().movex(76)));
            throw new AssertionError("combined geometry over 175 mm accepted");
        } catch (java.io.IOException expected) {
            check(!Files.exists(combined), "rejected combined model leaves no partial STL");
        }
        AtomicReference<Throwable> failure = new AtomicReference<>();
        AtomicInteger posts = new AtomicInteger();
        AtomicReference<String> key = new AtomicReference<>();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/api/jobs", exchange -> {
            try {
                var headers = exchange.getRequestHeaders();
                check("standard".equals(headers.getFirst("X-Quality")), "standard quality");
                check("any".equals(headers.getFirst("X-Colour")), "any colour");
                check("Måne og stjerne.stl".equals(URLDecoder.decode(headers.getFirst("X-Filename"), StandardCharsets.UTF_8)), "Danish filename");
                check(Arrays.equals(Files.readAllBytes(stl), exchange.getRequestBody().readAllBytes()), "same complete STL");
                String id = headers.getFirst("X-Request-ID");
                if (key.get() == null) key.set(id); else check(key.get().equals(id), "retry reuses identity");
                if (posts.incrementAndGet() == 1) { exchange.close(); return; } // committed, receipt lost
                byte[] response = "{\"id\":\"one-job-only\",\"status\":\"queued\"}".getBytes(StandardCharsets.UTF_8);
                exchange.sendResponseHeaders(201, response.length);
                exchange.getResponseBody().write(response);
            } catch (Throwable error) { failure.set(error); }
            finally { exchange.close(); }
        });
        server.createContext("/api/state", exchange -> {
            byte[] response = "{\"ready\":0,\"queued\":1,\"jobs\":[],\"printers\":[]}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, response.length);
            exchange.getResponseBody().write(response); exchange.close();
        });
        server.start();
        try {
            PrintQueueClient client = new PrintQueueClient(URI.create("http://127.0.0.1:" + server.getAddress().getPort()), null);
            String request = UUID.randomUUID().toString();
            try { client.submit(stl, "Måne og stjerne.stl", request); throw new AssertionError("lost response accepted"); }
            catch (java.io.IOException expected) { }
            check(client.submit(stl, "Måne og stjerne.stl", request).get("id").getAsString().equals("one-job-only"), "retry gets receipt");
            check(client.state().get("queued").getAsInt() == 1, "busy queue state readable");
            if (failure.get() != null) throw new AssertionError(failure.get());
            check(posts.get() == 2, "only the two intentional submissions");
            System.out.println("PASS: combined STL, holes excluded, empty rejected, Standard/any, Danish name, lost-receipt retry and queue state.");
        } finally {
            server.stop(0);
            Files.deleteIfExists(stl); Files.deleteIfExists(exact); Files.deleteIfExists(oversized);
            Files.deleteIfExists(folder.resolve("empty.stl"));
            Files.deleteIfExists(combined); Files.delete(folder);
        }
    }
    private static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
}
