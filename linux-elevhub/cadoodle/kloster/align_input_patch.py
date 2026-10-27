"""Keep alignment progress in the editor and capture the chosen axis before dispatch."""


def patch_align_input(fetch, replace_once, commit, generated):
    active = generated / "src/main/java/com/commonwealthrobotics/ActiveProject.java"
    text = active.read_text(encoding="utf-8")
    text = replace_once(text, "private void timeoutThread(CaDoodleOperation h, Thread t) {", """private void timeoutThread(CaDoodleOperation h, Thread t) {
        if (t == null) return;
        if (h instanceof com.neuronrobotics.bowlerstudio.scripting.cadoodle.Align) {
            com.commonwealthrobotics.kloster.OperationStatus.watch(this, t, "Justerer figurer…");
            return;
        }""")
    text = replace_once(text,
        "Thread t = get().regenerateFrom(source);\n\t\tif (t == null)\n\t\t\treturn null;",
        """Thread t = get().regenerateFrom(source);
        if (t == null) return null;
        if (source instanceof com.neuronrobotics.bowlerstudio.scripting.cadoodle.Align) {
            com.commonwealthrobotics.kloster.OperationStatus.watch(this, t, "Justerer figurer…");
            return t;
        }""")
    active.write_text(text, encoding="utf-8")

    handle, text = fetch("CaDoodle-Application", commit,
        "src/main/java/com/commonwealthrobotics/align/AlignHandle.java", generated)
    # The old callback scheduled a worker before setting the clicked axis. A
    # quick worker could therefore commit the previous alignment instead.
    text = replace_once(text, "onClickEvent = event -> {\n\t\t\t\tonClick.run();",
        "onClickEvent = event -> {\n\t\t\t\tsetMyOperation();\n\t\t\t\tonClick.run();")
    text = replace_once(text,
        "getHandle().removeEventFilter(MouseEvent.MOUSE_CLICKED, onClickEvent);\n\t\t\t\tsetMyOperation();",
        "getHandle().removeEventFilter(MouseEvent.MOUSE_CLICKED, onClickEvent);")
    handle.write_text(text, encoding="utf-8")
    manager, text = fetch("CaDoodle-Application", commit,
        "src/main/java/com/commonwealthrobotics/align/AlignManager.java", generated)
    start = text.index("\t\t\t\tsetAlignemntSelected(true);")
    end = text.index("\n\n\t\t\t});", start)
    text = text[:start] + """                setAlignemntSelected(true);
                // Capture the chosen axes now; previews must not delay a commit
                // or mutate an operation already present in project history.
                if (operation == null) return;
                final Align captured = operation.copy();
                final var project = ap.get();
                com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
                    if (ap.get() != project) return;
                    Thread task = ap.addOp(captured);
                    if (task != null) {
                        try { task.join(); }
                        catch (InterruptedException interrupted) { Thread.currentThread().interrupt(); }
                    }
                });
                recompute(null, project.getBoundsCache());""" + text[end:]
    manager.write_text(text, encoding="utf-8")
    return [active, handle, manager]
