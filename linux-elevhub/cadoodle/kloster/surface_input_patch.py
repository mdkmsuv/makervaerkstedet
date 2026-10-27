"""Capture native surface inputs and preserve subsequent selection intent."""


def patch_surface_input(fetch, replace_once, app_commit, generated):
    session = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = session.read_text(encoding="utf-8")
    text = replace_once(text, "\tprivate void addToSelected(CSG name) {", """    private long selectionIntentRevision;

    public synchronized long getSelectionIntentRevision() {
        return selectionIntentRevision;
    }

    protected synchronized void markSelectionIntent() {
        ++selectionIntentRevision;
    }

    private void addToSelected(CSG name) {
        markSelectionIntent();
        restoreSelectedModel(name);
    }

    // Rebinding the same intended names to regenerated CSGs is not a new
    // selection request, even when the operation removed an original name.
    private void restoreSelectedModel(CSG name) {""")
    start = text.index("\tprivate void displayCurrent(CaDoodleOperation source)")
    end = text.index("\n\tpublic void clearScreen()", start)
    block = text[start:end]
    block = replace_once(block, "addToSelected(c);", "restoreSelectedModel(c);")
    text = text[:start] + block + text[end:]
    start = text.index("\tprivate void resetSelectedCSGsFromCurrentState(List<CSG> cs)")
    end = text.index("\n\t// public void clearBoundsCache", start)
    block = text[start:end]
    block = replace_once(block, "clearInternalSelection();", "clearSelectionForRefresh();")
    block = replace_once(block, "addToSelected(c);", "restoreSelectedModel(c);")
    text = text[:start] + block + text[end:]
    text = replace_once(text, "\tprivate void removeSelected(CSG target) {", "\tprivate void removeSelected(CSG target) {\n        markSelectionIntent();")
    text = replace_once(text, "\tprivate void clearInternalSelection() {", """    private void clearInternalSelection() {
        markSelectionIntent();
        clearSelectionForRefresh();
    }

    private void clearSelectionForRefresh() {""")
    text = replace_once(text, "\tpublic void selectAll(Iterable<String> names) {", "\tpublic void selectAll(Iterable<String> names) {\n        markSelectionIntent();")
    text = replace_once(text, "\tpublic void selectAll() {", "\tpublic void selectAll() {\n        markSelectionIntent();")
    text = replace_once(text, "\tpublic void setSelected(LinkedHashSet<CSG> selected) {", "\tpublic void setSelected(LinkedHashSet<CSG> selected) {\n        markSelectionIntent();")
    session.write_text(text, encoding="utf-8")
    sources = [session]
    for fillet in (True, False):
        classname = "FilletUIManager" if fillet else "ExtrudeUIManager"
        path, text = fetch("CaDoodle-Application", app_commit,
            "src/main/java/com/commonwealthrobotics/fillet/" + classname + ".java", generated)
        start = text.index("\t\tworkplane.setOnSelectEvent(() -> {")
        end = text.index("\n\t\tworkplane.activate(true);", start)
        construction = ("new FilletChamfer().setWorkplane(pose).setToFillet(names)" if fillet
            else "new ExtrudeSurface().setSlicePlane(pose).setToExtrude(names).setSweep(sweep)")
        workplane_update = "" if fillet else """            file.setWorkplane(ground ? new TransformNR() : pose.copy());
"""
        temporary = "" if fillet else "                workplane.setTemporaryPlane();\n"
        # Fully-qualified new types keep the pinned imports unchanged.
        text = text[:start] + """        final var activationFile = ap.get();
        final boolean[] accepted = {false};
        workplane.setOnSelectEvent(() -> {
            if (!javafx.application.Platform.isFxApplicationThread())
                throw new IllegalStateException("Capture surface placement on the JavaFX thread");
            if (accepted[0] || ap.get() != activationFile || !workplane.isClicked()) return;
            accepted[0] = true;
            final var file = activationFile;
            final var pose = workplane.getCurrentAbsolutePose().copy();
            final boolean ground = workplane.isClickOnGround();
            final Set<String> names = new java.util.LinkedHashSet<>();
            for (CSG shape : selected) names.add(shape.getName());
            if (names.isEmpty()) return;
            final var op = """ + construction + """;
            op.setCaDoodleFile(file);
            // Clear only the selection belonging to this accepted placement.
            // Completion must not clear a newer user selection.
            session.clearSelection();
            final long intent = session.getSelectionIntentRevision();
            session.setMode(SpriteDisplayMode.Default);
            session.updateControls();
            com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
                if (ap.get() != file) return;
""" + workplane_update + """                Thread task = ap.addOp(op);
                try {
                    if (task != null) task.join();
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    return;
                }
                javafx.application.Platform.runLater(() -> {
                    if (ap.get() != file) return;
                    if (session.getSelectionIntentRevision() == intent) {
                        // This method applies selection synchronously. selectAll
                        // would enqueue another unguarded worker after this check.
                        session.selectAllFromCurrentState(op.getNamesAddedInThisOperation());
                        workplane.placeWorkplaneVisualization();
""" + temporary + """                        ruler.disableRulerMode();
                        session.updateControls();
                    }
                    session.save();
                });
            });
        });
""" + text[end:]
        path.write_text(text, encoding="utf-8")
        sources.append(path)
    return sources
