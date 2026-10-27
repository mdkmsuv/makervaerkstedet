package com.commonwealthrobotics.kloster;

import com.commonwealthrobotics.ActiveProject;
import com.commonwealthrobotics.controls.SelectionSession;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.*;
import java.util.UUID;
import javafx.application.Platform;
import javafx.geometry.Pos;
import javafx.scene.Node;
import javafx.scene.control.*;
import javafx.scene.input.KeyCode;
import javafx.scene.layout.*;

/** Explicit confirmation, durable retry identity and an honest queue receipt. */
final class PrintSubmission {
    private static final Gson JSON = new Gson();
    private final ActiveProject ap;
    private final SelectionSession session;
    private final Pane root;
    private final Path folder = Path.of(System.getProperty("user.home"), ".local", "state", "elevhub", "cadoodle-print");
    private StackPane overlay;
    private Node content;
    private Label heading, message, details;
    private Button yes, cancel;
    private ProgressIndicator progress;
    private boolean busy;
    private Pending pending;

    private static final class Pending {
        String requestId, filename, endpoint;
    }

    PrintSubmission(ActiveProject ap, SelectionSession session, Pane root) {
        this.ap = ap; this.session = session; this.root = root;
    }

    Button button() {
        Button send = new Button("Send til ledig printer");
        send.setId("kloster-send-print");
        send.getStyleClass().add("kloster-primary");
        send.setTooltip(new Tooltip("Send hele den synlige model med Standard og valgfri farve. Optagede printere giver ventekø."));
        send.setOnAction(event -> confirm());
        return send;
    }

    private Path manifest() { return folder.resolve("pending.json"); }
    private Path model(Pending item) { return folder.resolve(item.requestId + ".stl"); }

    private Pending readPending() throws IOException {
        if (!Files.exists(manifest())) return null;
        try {
            Pending item = JSON.fromJson(Files.readString(manifest()), Pending.class);
            UUID.fromString(item.requestId);
            if (item.filename == null || item.endpoint == null || !Files.isRegularFile(model(item))) throw new IOException();
            return item;
        } catch (Exception error) {
            throw new IOException("En tidligere indsendelse kunne ikke læses. Bed en voksen kontrollere printkøen, før du sender igen.");
        }
    }

    private void savePending(Pending item) throws IOException {
        Path temporary = folder.resolve("pending.tmp");
        Files.writeString(temporary, JSON.toJson(item));
        Files.move(temporary, manifest(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
    }

    private void confirm() {
        if (overlay != null) return;
        heading = new Label("Send til ledig printer");
        heading.getStyleClass().add("kloster-print-heading");
        message = new Label("Er du sikker på, at du er færdig med din tegning og vil sende den til print på en af de ledige printere?");
        message.setWrapText(true);
        message.getStyleClass().add("kloster-print-question");
        details = new Label("Standardkvalitet · Ligemeget med farven\nAlle synlige, solide figurer sendes samlet. Hvis der ikke er en ledig printer, sættes printet automatisk i kø.");
        details.setWrapText(true);
        details.getStyleClass().add("kloster-print-details");
        yes = new Button("Ja, send til print");
        yes.getStyleClass().add("kloster-primary");
        yes.setOnAction(event -> send());
        cancel = new Button("Nej, tegn videre");
        cancel.setOnAction(event -> close());
        progress = new ProgressIndicator();
        progress.setPrefSize(24, 24);
        progress.setVisible(false);
        progress.managedProperty().bind(progress.visibleProperty());
        Region spacer = new Region();
        HBox.setHgrow(spacer, Priority.ALWAYS);
        HBox actions = new HBox(12, progress, spacer, cancel, yes);
        actions.setAlignment(Pos.CENTER_RIGHT);
        VBox card = new VBox(20, heading, message, details, actions);
        card.setMaxSize(600, Region.USE_PREF_SIZE);
        card.getStyleClass().add("kloster-print-card");
        overlay = new StackPane(card);
        overlay.getStyleClass().add("kloster-print-overlay");
        AnchorPane.setTopAnchor(overlay, 0.0);
        AnchorPane.setBottomAnchor(overlay, 0.0);
        AnchorPane.setLeftAnchor(overlay, 0.0);
        AnchorPane.setRightAnchor(overlay, 0.0);
        content = root.getChildren().get(0);
        content.setDisable(true);
        root.getChildren().add(overlay);
        overlay.addEventFilter(javafx.scene.input.KeyEvent.KEY_PRESSED, event -> {
            if (event.getCode() == KeyCode.ESCAPE) { if (!busy) close(); event.consume(); }
            if (event.getCode() == KeyCode.TAB) {
                if (!busy) {
                    if (root.getScene().getFocusOwner() == cancel && yes.isVisible()) yes.requestFocus();
                    else cancel.requestFocus();
                }
                event.consume();
            }
        });
        try {
            pending = readPending();
            if (pending != null) pendingMessage();
        } catch (IOException error) {
            message.setText(error.getMessage());
            yes.setVisible(false); yes.setManaged(false);
        }
        // Returning to the drawing is the keyboard default; sending requires an explicit choice.
        cancel.requestFocus();
    }

    private void pendingMessage() {
        message.setText("Den tidligere indsendelse mangler en kvittering. Vil du prøve den samme indsendelse igen?");
        details.setText(pending.filename + "\nDen gemte model sendes med samme job-id, så et forbindelsesproblem ikke giver dobbeltprint. Senere ændringer i tegningen er ikke med.");
        yes.setText("Prøv samme print igen");
    }

    private void send() {
        if (busy) return;
        if (pending == null && (ap.get().isOperationRunning() || ap.get().getPercentInitialized() < 0.99 || session.isInOperationMode())) {
            message.setText("Afslut den igangværende ændring, før du sender din tegning til print.");
            return;
        }
        busy = true;
        yes.setDisable(true); cancel.setDisable(true); progress.setVisible(true);
        heading.setText("Sender din tegning");
        message.setText("Modellen klargøres og sendes til Printværkstedet …");
        var project = ap.get();
        // Use the modelling executor so a previous edit completes before taking the export snapshot.
        session.submit(() -> {
            boolean accepted = false;
            try {
                PrintQueueClient client = PrintQueueClient.configured();
                if (pending == null) {
                    if (ap.get() != project || project.isOperationRunning()) throw new IOException("Tegningen blev ændret. Prøv igen, når CaDoodle er færdig.");
                    Files.createDirectories(folder);
                    Pending item = new Pending();
                    item.requestId = UUID.randomUUID().toString();
                    String title = project.getMyProjectName().replaceAll("[\\\\/\\p{Cntrl}]", "_");
                    item.filename = title.substring(0, Math.min(110, title.length())) + ".stl";
                    item.endpoint = client.endpoint();
                    PrintModel.write(model(item), session.getAllVisible());
                    savePending(item); // durable before any bytes can reach the server
                    pending = item;
                }
                if (!pending.endpoint.equals(client.endpoint())) throw new IOException("Printværkstedets adresse er ændret. Bed en voksen kontrollere den tidligere indsendelse.");
                JsonObject receipt = client.submit(model(pending), pending.filename, pending.requestId);
                accepted = true;
                String id = receipt.get("id").getAsString();
                // Failure to store a receipt must never turn an accepted print into a failed print.
                try {
                    receipt.addProperty("filename", pending.filename);
                    receipt.addProperty("request_id", pending.requestId);
                    Files.writeString(folder.resolve(pending.requestId + "-receipt.json"), JSON.toJson(receipt));
                    Files.deleteIfExists(manifest());
                    pending = null;
                } catch (IOException logError) { System.err.println("Print accepted; local receipt could not be saved: " + id); }
                String status = receipt.has("status") ? receipt.get("status").getAsString() : "queued";
                String printer = "";
                try {
                    Thread.sleep(1200);
                    JsonObject state = client.state();
                    for (var row : state.getAsJsonArray("jobs")) {
                        JsonObject job = row.getAsJsonObject();
                        if (!id.equals(job.get("id").getAsString())) continue;
                        status = job.get("status").getAsString();
                        if (job.has("printer_id") && !job.get("printer_id").isJsonNull()) {
                            for (var p : state.getAsJsonArray("printers")) {
                                if (p.getAsJsonObject().get("id").equals(job.get("printer_id"))) printer = p.getAsJsonObject().get("name").getAsString();
                            }
                        }
                    }
                } catch (Exception ignored) { /* The POST receipt is sufficient evidence of acceptance. */ }
                String finalStatus = status, finalPrinter = printer;
                Platform.runLater(() -> receipt(id, finalStatus, finalPrinter));
            } catch (Exception error) {
                if (accepted) return;
                if (error instanceof PrintQueueClient.Rejected rejection && rejection.definite) {
                    try { Files.deleteIfExists(manifest()); pending = null; } catch (IOException ignored) { }
                }
                Platform.runLater(() -> failed(error));
            }
        });
    }

    private void receipt(String id, String status, String printer) {
        busy = false; progress.setVisible(false);
        heading.setText("Printet er modtaget");
        message.setText(switch (status) {
            case "preparing", "dispatching", "starting" -> "Din model klargøres til print" + (printer.isBlank() ? "." : " på " + printer + ".");
            case "printing" -> "Din model bliver printet" + (printer.isBlank() ? "." : " på " + printer + ".");
            case "done" -> "Dit print er færdigt" + (printer.isBlank() ? "." : " på " + printer + ".");
            case "failed", "attention", "cancelled", "recovering" -> "Indsendelsen er modtaget, men printet kræver eftersyn. Bed en voksen kontrollere printkøen.";
            default -> "Din model er sat i kø. Den klargøres automatisk, når en passende printer er ledig.";
        });
        details.setText("Standardkvalitet · Ligemeget med farven\nKvittering: " + id.substring(0, Math.min(8, id.length())) + "\nDu kan tegne videre. Printværkstedet passer køen.");
        yes.setVisible(false); yes.setManaged(false);
        cancel.setDisable(false); cancel.setText("Tilbage til tegningen"); cancel.requestFocus();
    }

    private void failed(Exception error) {
        busy = false; progress.setVisible(false); yes.setDisable(false); cancel.setDisable(false);
        heading.setText("Printet blev ikke bekræftet");
        if (pending != null) {
            pendingMessage();
            details.setText(details.getText() + "\nKontrollér forbindelsen til Printværkstedet.");
        } else message.setText(error instanceof IOException ? error.getMessage() : "Modellen kunne ikke sendes. Prøv igen, eller bed en voksen om hjælp.");
        cancel.setText("Tilbage til tegningen"); cancel.requestFocus();
    }

    private void close() {
        if (busy) return;
        root.getChildren().remove(overlay);
        overlay = null;
        content.setDisable(false);
        session.setKeyBindingFocus();
    }
}
