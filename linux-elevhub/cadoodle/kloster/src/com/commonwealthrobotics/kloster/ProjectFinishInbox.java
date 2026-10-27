package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.google.gson.*;
import java.io.IOException;
import java.nio.file.*;
import javafx.application.Platform;
import javafx.scene.layout.Pane;

/** Local asynchronous handoff; a request never grants permission to discard work. */
public final class ProjectFinishInbox {
    private static final Gson JSON = new Gson();
    private final Path root;
    public ProjectFinishInbox(ActiveProject project, Pane owner) {
        root = ProjectNaming.projectsRoot().resolve(".elevhub/finish-requests");
        Thread watcher = new Thread(() -> {
            while (!Thread.currentThread().isInterrupted()) {
                try {
                    if (Files.isDirectory(root) && !Files.isSymbolicLink(root)) try (var files = Files.list(root)) {
                        for (Path request : files.filter(p -> p.getFileName().toString().matches("[a-f0-9]{32}\\.ready")).sorted().toList()) {
                            String id = request.getFileName().toString().substring(0, 32);
                            try {
                                validate(request, id);
                                if (!project.isOpen() || project.get() == null || !project.get().isInitialized()) continue;
                                String action = JsonParser.parseString(Files.readString(request)).getAsJsonObject().get("action").getAsString();
                                if ("show".equals(action)) {
                                    Files.delete(request);
                                    Platform.runLater(() -> ProjectStartDialog.activate(owner));
                                    continue;
                                }
                                Path status = root.resolve(id + ".status.json");
                                if (Files.exists(status)) { Files.deleteIfExists(request); continue; }
                                writeStatus(root, id, "pending", "Afventer dit valg i CaDoodle.");
                                Files.delete(request);
                                Platform.runLater(() -> ProjectCloseGuard.requestFinish(owner, result -> {
                                    try { writeStatus(root, id, result, switch (result) {
                                        case "completed" -> "Projektet er afsluttet. En ny tom tegning er klar.";
                                        case "cancelled" -> "Du kan arbejde videre i CaDoodle.";
                                        default -> "CaDoodle er optaget. Prøv igen.";
                                    }); } catch (IOException failure) { failure.printStackTrace(); }
                                }));
                            } catch (Exception failure) {
                                writeStatus(root, id, "error", "Afslutningen kunne ikke startes. Prøv igen.");
                                Files.deleteIfExists(request);
                            }
                        }
                    }
                    Thread.sleep(300);
                } catch (InterruptedException stopped) { return; }
                catch (Exception failure) { System.err.println("Finish inbox: " + failure.getClass().getSimpleName()); }
            }
        }, "Elevhub-finish-inbox");
        watcher.setDaemon(true); watcher.start();
    }
    static void validate(Path request, String id) throws IOException {
        if (Files.isSymbolicLink(request) || Files.size(request) > 4096) throw new IOException("Invalid request");
        try {
            JsonObject data = JsonParser.parseString(Files.readString(request)).getAsJsonObject();
            double age = System.currentTimeMillis() / 1000.0 - data.get("created").getAsDouble();
            if (!id.matches("[a-f0-9]{32}") || !id.equals(data.get("id").getAsString())
                    || !java.util.Set.of("finish", "show").contains(data.get("action").getAsString()) || !Double.isFinite(age) || age < -5 || age > 120)
                throw new IOException("Invalid request");
        } catch (RuntimeException failure) { throw new IOException("Invalid request", failure); }
    }
    static void writeStatus(Path root, String id, String state, String message) throws IOException {
        JsonObject response = new JsonObject(); response.addProperty("id", id);
        response.addProperty("state", state); response.addProperty("message", message);
        Path temporary = root.resolve(id + ".status.tmp"), target = root.resolve(id + ".status.json");
        Files.writeString(temporary, JSON.toJson(response));
        Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }
}
