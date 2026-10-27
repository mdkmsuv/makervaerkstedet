"""Serialize ordinary toolbar model edits with captured FX selection."""


def _replace_method(text, signature, replacement):
    start = text.index(signature)
    opening = text.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[:start] + replacement + text[end:]


def patch_mutation_order(replace_once, generated):
    path = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = path.read_text(encoding="utf-8")
    helper = """    /** The caller captures operation inputs on FX; workers never reread selection. */
    private void queueNativeMutation(Runnable mutation) {
        final var file = ap.get();
        com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
            if (ap.get() == file) mutation.run();
        });
    }

    private void applyNativeOperation(CaDoodleOperation operation) {
        try {
            Thread task = ap.addOp(operation);
            if (task != null) task.join();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            Log.error(interrupted);
        }
    }

    private void queueNativeOperation(CaDoodleOperation operation) {
        queueNativeMutation(() -> applyNativeOperation(operation));
    }

"""
    text = replace_once(text, "\tpublic void onDelete() {", helper + "\tpublic void onDelete() {")
    text = _replace_method(text, "\tpublic void onDelete() {", """\tpublic void onDelete() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (!names.isEmpty()) queueNativeOperation(new Delete().setNames(names));
    }""")
    text = _replace_method(text, "\tpublic void lockToggle() {", """\tpublic void lockToggle() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty()) return;
        queueNativeMutation(() -> {
            boolean locked = ap.get().getSelect(names).stream().anyMatch(CSG::isLock);
            applyNativeOperation(locked
                    ? new UnLock().setNames(names) : new Lock().setNames(names));
        });
    }""")
    text = _replace_method(text, "\tpublic void wireMeshModeToggle() {", """\tpublic void wireMeshModeToggle() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty()) return;
        queueNativeMutation(() -> {
            boolean wire = ap.get().getSelect(names).stream().anyMatch(CSG::isWireFrame);
            applyNativeOperation(new WireMeshView().setNames(names).setToMesh(!wire));
        });
    }""")
    text = _replace_method(text, "\tpublic void showAll() {", """\tpublic void showAll() {
        queueNativeMutation(() -> {
            List<String> names = new ArrayList<>();
            for (CSG shape : ap.get().getCurrentState())
                if (shape.isHide()) names.add(shape.getName());
            if (!names.isEmpty()) applyNativeOperation(new Show().setNames(names));
        });
    }""")
    text = _replace_method(text, "\tpublic void onHideShowOperation() {", """\tpublic void onHideShowOperation() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty()) return;
        final var file = ap.get();
        queueNativeMutation(() -> {
            List<CSG> selectedNow = ap.get().getSelect(names);
            if (selectedNow.isEmpty()) return;
            boolean hidden = selectedNow.stream().allMatch(CSG::isHide);
            applyNativeOperation(hidden ? new Show().setNames(names) : new Hide().setNames(names));
            Platform.runLater(() -> {
                if (ap.get() == file) updateShowHideButton();
            });
        });
    }""")
    text = _replace_method(text, "\tpublic void setColor(Color value) {", """\tpublic void setColor(Color value) {
        List<String> names = List.copyOf(selectedSnapshot());
        if (!names.isEmpty()) queueNativeOperation(new ToSolid().setNames(names).setColor(value));
    }""")
    text = _replace_method(text, "\tpublic void setToHole() {", """\tpublic void setToHole() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty()) return;
        queueNativeMutation(() -> {
            if (ap.get().getSelect(names).stream().anyMatch(shape -> !shape.isHole()))
                applyNativeOperation(new ToHole().setNames(names));
        });
    }""")
    text = _replace_method(text, "\tpublic void setToSolid() {", """\tpublic void setToSolid() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty()) return;
        queueNativeMutation(() -> {
            if (ap.get().getSelect(names).stream().anyMatch(CSG::isHole))
                applyNativeOperation(new ToSolid().setNames(names));
        });
    }""")
    text = _replace_method(text, "\tpublic void onDrop() {", """\tpublic void onDrop() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.isEmpty() || moveLock()) return;
        final var file = ap.get();
        final TransformNR workplanePose = file.getWorkplane().copy();
        queueNativeMutation(() -> {
            for (CSG shape : file.getSelect(names)) {
                try {
                    Thread task = ap.addOp(new MoveCenter().setDropMode(workplanePose)
                            .setNames(List.of(shape.getName()), file));
                    if (task != null) task.join();
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                    Log.error(interrupted);
                    return;
                }
            }
        });
    }""")
    text = _replace_method(text, "\tpublic void setUserDefinedName(String newText) {", """\tpublic void setUserDefinedName(String newText) {
        List<CSG> selectedNow = getCurrentStateSelected();
        if (selectedNow == null || selectedNow.isEmpty()) return;
        CSG shape = selectedNow.get(0);
        if (shape.getUserDefinedName().contentEquals(newText)) return;
        queueNativeOperation(new SetUserDefinedName().setTarget(shape.getName())
                .setNewUserDefinedName(newText));
    }""")
    text = _replace_method(text, "\tpublic void onGroup(boolean hull, boolean intersect) {", """\tpublic void onGroup(boolean hull, boolean intersect) {
        List<String> names = List.copyOf(selectedSnapshot());
        if (names.size() <= 1 && !hull) {
            updateControlsDisplayOfSelected();
            updateRobotLab.run();
            return;
        }
        Group operation = new Group().setNames(names);
        operation.setHull(hull);
        operation.setIntersect(intersect);
        final var file = ap.get();
        queueNativeMutation(() -> {
            try {
                Thread task = ap.addOp(operation);
                if (task == null) return;
                task.join();
                List<String> added = List.copyOf(operation.getNamesAddedInThisOperation());
                List<CSG> resulting = new ArrayList<>();
                for (CSG shape : file.getCurrentState())
                    if (added.contains(shape.getName()) && !shape.isInGroup()) resulting.add(shape);
                Platform.runLater(() -> {
                    if (ap.get() != file) return;
                    clearInternalSelection();
                    for (CSG shape : resulting) addToSelected(shape);
                    fireSelectionChanged();
                    updateControlsDisplayOfSelected();
                    updateRobotLab.run();
                });
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                Log.error(interrupted);
            }
        });
    }""")
    text = _replace_method(text, "\tpublic void onUngroup() {", """\tpublic void onUngroup() {
        List<String> names = List.copyOf(selectedSnapshot());
        if (!isAGroupSelected()) return;
        UnGroup operation = new UnGroup().setNames(names);
        final var file = ap.get();
        queueNativeMutation(() -> {
            try {
                Thread task = ap.addOp(operation);
                if (task == null) return;
                task.join();
                List<String> added = List.copyOf(operation.getNamesAddedInThisOperation());
                Platform.runLater(() -> {
                    if (ap.get() != file) return;
                    clearInternalSelection();
                    for (String name : added) {
                        CSG shape = getSelectedCSG(name);
                        if (shape != null) addToSelected(shape);
                    }
                    fireSelectionChanged();
                    updateControlsDisplayOfSelected();
                    updateRobotLab.run();
                });
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                Log.error(interrupted);
            }
        });
    }""")
    # XOR is a compound native transaction. Keep all of its joins in one queue
    # entry, but freeze its original operands before scheduling the worker.
    text = replace_once(text, """\tpublic void onXor() {
\t\tif (ap.get().isOperationRunning()) {
\t\t\tcom.neuronrobotics.sdk.common.Log.error("Ignoring operation because previous had not finished!");
\t\t\treturn;
\t\t}

\t\tif (getSelected().size() > 1) {
\t\t\tsubmit(() -> {
\t\t\t\ttry {
\t\t\t\t\tList<String> selectedSnapshot = selectedSnapshot();""",
        """\tpublic void onXor() {
        List<String> capturedNames = List.copyOf(selectedSnapshot());
        if (capturedNames.size() > 1) {
            queueNativeMutation(() -> {
                try {
                    List<String> selectedSnapshot = capturedNames;""")
    for expression in ("performPaste(paste)", "performPaste(dist)"):
        text = text.replace("submit(() -> " + expression + ");",
                            "queueNativeMutation(() -> " + expression + ");")
    busy_gate = (
        "\tprivate void performPaste(AbstractAddFrom op) {\n"
        "\t\tif (ap.get().isOperationRunning()) {\n"
        "\t\t\tcom.neuronrobotics.sdk.common.Log.error("
        "\"Ignoring operation because previous had not finished!\");\n"
        "\t\t\treturn;\n\t\t}\n"
    )
    text = replace_once(text, busy_gate, "\tprivate void performPaste(AbstractAddFrom op) {\n")
    path.write_text(text, encoding="utf-8")
    return [path]
