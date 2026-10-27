"""Keep pattern/paste result selection and copy buffer in one guarded FX handoff."""


def patch_pattern_input(replace_once, generated):
    path = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = path.read_text(encoding="utf-8")
    for method, operation in (("runBoltHole", "RadialDistribution"), ("runHexDistribute", "LinearDistribution")):
        old = f"\tpublic void {method}() {{\n\t\t{operation} dist = new {operation}().setWorkplane(getWorkplane()).setNames(selectedSnapshot());"
        new = f"""    public void {method}() {{
        final List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty()) return;
        {operation} dist = new {operation}().setWorkplane(getWorkplane().copy()).setNames(names);"""
        text = replace_once(text, old, new)
    # Four existing call sites share the same result handoff.
    for variable in ("paste", "dist"):
        old = f"queueNativeMutation(() -> performPaste({variable}));"
        if text.count(old) != 2:
            raise RuntimeError(f"Unexpected paste/pattern call sites for {variable}")
        text = text.replace(old, f"queuePaste({variable});")
    start = text.index("\tprivate void performPaste(AbstractAddFrom op) {")
    end = text.index("\n\tpublic void onCruise()", start)
    text = text[:start] + '''    private void queuePaste(AbstractAddFrom op) {
        final var file = ap.get();
        final long intent = getSelectionIntentRevision();
        queueNativeMutation(() -> performPaste(op, file, intent));
    }

    private void performPaste(AbstractAddFrom op, CaDoodleFile file, long intent) {
        try {
            Thread task = ap.addOp(op);
            if (task == null) return;
            task.join();
            final List<String> added = List.copyOf(op.getNamesAddedInThisOperation());
            // Selection and clipboard describe the same completed result before
            // the next queued model edit runs. Never overwrite a newer selection.
            java.util.concurrent.FutureTask<Void> finish = new java.util.concurrent.FutureTask<>(() -> {
                if (ap.get() == file && getSelectionIntentRevision() == intent) {
                    selectAllFromCurrentState(added);
                    copySetinternal = List.copyOf(selectedSnapshot());
                    setKeyBindingFocus();
                }
                return null;
            });
            Platform.runLater(finish);
            finish.get();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
        } catch (java.util.concurrent.ExecutionException | CadoodleConcurrencyException error) {
            Log.error(error);
        }
    }
''' + text[end:]
    path.write_text(text, encoding="utf-8")
    return [path]
