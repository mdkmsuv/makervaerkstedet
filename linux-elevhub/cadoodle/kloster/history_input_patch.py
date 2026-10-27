"""Preserve group snapshots and order Undo/Redo after pending model edits."""

KERNEL_COMMIT = "457a64691504f1231ae5e6d5fd1d99577d96a5b4"


def patch_history(fetch, replace_once, generated):
    path, text = fetch("bowler-script-kernel", KERNEL_COMMIT,
        "src/main/java/com/neuronrobotics/bowlerstudio/scripting/cadoodle/UnGroup.java", generated)
    # Non-parametric shapes otherwise alias a member of the previous cached
    # state. Removing its membership makes Undo show both group and members.
    text = replace_once(text, "CSG transformed = csg;", "CSG transformed = csg.clone();")
    path.write_text(text, encoding="utf-8")
    controller = generated / "src/main/java/com/commonwealthrobotics/MainController.java"
    text = controller.read_text(encoding="utf-8")
    start = text.index("\t@FXML\n\tvoid onRedo(ActionEvent event) {")
    end = text.index("\n\t@FXML\n\tvoid onPaste", start)
    text = text[:start] + '''    private void queueHistory(boolean redo) {
        final CaDoodleFile file = ap.get();
        if (file == null) return;
        session.abortInteraction();
        com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
            if (ap.get() != file) return;
            if (redo) {
                if (file.isForwardAvailable()) file.forward();
            } else if (file.isBackAvailable()) file.back();
            javafx.application.Platform.runLater(() -> {
                if (ap.get() == file) session.setKeyBindingFocus();
            });
        });
    }

    @FXML
    void onRedo(ActionEvent event) { queueHistory(true); }

    @FXML
    void onUndo(ActionEvent event) { queueHistory(false); }
''' + text[end:]
    text = replace_once(text, "session.submit(() -> ap.get().back());", "queueHistory(shift);")
    text = replace_once(text, "session.submit(() -> ap.get().forward());", "queueHistory(true);")
    controller.write_text(text, encoding="utf-8")
    return [path]
