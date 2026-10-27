"""Pinned application patches for numeric dimension commits, separate from drag gestures."""


def patch_dimension_input(fetch, replace_once, commit, generated):
    resize, text = fetch("CaDoodle-Application", commit,
        "src/main/java/com/commonwealthrobotics/controls/ResizeSessionManager.java", generated)
    text = replace_once(text, "private final SelectionSession session;",
        "private final SelectionSession session;\n\tprivate final ActiveProject activeProject;")
    text = replace_once(text, "this.session = session;",
        "this.session = session;\n\t\tthis.activeProject = ap;")
    method = '''
    /** Numeric edits use canonical model bounds, not an expired mouse gesture. */
    public void setDimension(int axis, double value) throws NowFXThreadException {
        if (!Platform.isFxApplicationThread()) throw new NowFXThreadException();
        if (!resizeAllowed || session.isLocked() || !Double.isFinite(value) || value <= 0) return;
        final var project = activeProject.get();
        final List<String> names = List.copyOf(session.selectedSnapshot());
        if (names.isEmpty()) return;
        final TransformNR workplane = project.getWorkplane().copy();
        ResizingHandle selectedHandle = controls.stream().filter(ResizingHandle::isSelected)
                .findFirst().orElse(null);
        final boolean uniform = selectedHandle != null && selectedHandle.isUniform();
        final double anchorX = frontSelected() ? 0 : rearSelected() ? 1 : 0.5;
        final double anchorY = leftSelected() ? 0 : rightSelected() ? 1 : 0.5;

        // Finish only the numeric edit's temporary handles. Native drag callbacks
        // must not synthesize another preview/save while their offsets reset.
        beingUpdated = null;
        originalBounds = null;
        for (ResizingHandle handle : controls) handle.manipulator.set(0, 0, 0);
        resetSelected();
        controlSprites.setMode(SpriteDisplayMode.Default);
        // SelectionSession uses a multi-thread pool. Chain numeric commits so
        // quick successive values are applied in the order entered.
        com.commonwealthrobotics.kloster.ModelEditQueue.submit(activeProject, () -> {
            if (activeProject.get() != project) return;
            List<CSG> current = new ArrayList<>();
            for (CSG shape : project.getCurrentState()) {
                if (names.contains(shape.getName())) {
                    if (shape.isNoScale()) return;
                    current.add(shape);
                }
            }
            if (current.size() != names.size()) return;
            Resize operation = com.commonwealthrobotics.kloster.DimensionResize.create(
                    current, workplane, axis, value, anchorX, anchorY, uniform);
            if (operation != null) {
                try {
                    Thread task = activeProject.addOp(operation);
                    if (task != null) task.join();
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            }
        });
    }

'''
    text = replace_once(text, "\tpublic void set(double x, double y, double z) throws NowFXThreadException {",
        method + "\tpublic void set(double x, double y, double z) throws NowFXThreadException {")
    resize.write_text(text, encoding="utf-8")

    sprites, text = fetch("CaDoodle-Application", commit,
        "src/main/java/com/commonwealthrobotics/controls/ControlSprites.java", generated)
    start = text.index("\t\tRunnable dimChange = () -> {")
    end = text.index("\n\t\tRunnable offsetxyChange", start)
    text = text[:start] + '''\t\tjava.util.function.IntConsumer dimChange = axis -> {
            ThreedNumber field = axis == 0 ? xdimen : axis == 1 ? ydimen : zdimen;
            if (field.canceled) { field.hide(); return; }
            // Capture only the edited axis on the FX thread. Reading all three
            // fields later can mix new input with stale gesture dimensions.
            double value = field.getMostRecentValue();
            try {
                scaleSession.setDimension(axis, value);
            } catch (NowFXThreadException error) {
                Log.error(error);
            }
        };
''' + text[end:]
    for axis, field in enumerate(("xdimen", "ydimen", "zdimen")):
        text = replace_once(text,
            f"{field} = new ThreedNumber(selection, workplaneOffset, dimChange,",
            f"{field} = new ThreedNumber(selection, workplaneOffset, () -> dimChange.accept({axis}),")
    sprites.write_text(text, encoding="utf-8")

    number, text = fetch("CaDoodle-Application", commit,
        "src/main/java/com/commonwealthrobotics/numbers/ThreedNumber.java", generated)
    # Enter/Escape already arrive on the FX thread. Do not move TextField reads,
    # validation and the commit callback to an unsynchronized worker thread.
    worker = "new Thread(() -> {\n\t\t\t\t\trunEnter();\n\t\t\t\t}).start();"
    if text.count(worker) != 2:
        raise RuntimeError("Pinned number commit handlers changed")
    text = text.replace(worker, "runEnter();")
    text = replace_once(text,
        "if (event.getCode() == KeyCode.ENTER) {\n\t\t\t\tevent.consume();",
        "if (event.getCode() == KeyCode.ENTER) {\n\t\t\t\tevent.consume();\n\t\t\t\tcanceled = false;")
    text = replace_once(text, "private boolean lockout = false;", """private boolean lockout = false;
    private volatile long editGeneration;
    private boolean editing, dirty, writingText, committing;""")
    start = text.index("\t\t// Select number at edit box select")
    end = text.index("\n\t\ttextField.setMinWidth", start)
    text = text[:start] + """        textField.focusedProperty().addListener((obs, wasFocused, focused) -> {
            if (focused) {
                beginEdit();
                final long generation = editGeneration;
                Platform.runLater(() -> {
                    if (generation == editGeneration && textField.isFocused()
                            && textField.isVisible() && !dirty) textField.selectAll();
                });
            } else {
                finishFocusEdit();
            }
        });
        textField.textProperty().addListener((obs, before, after) -> {
            if (!writingText && !lockout) {
                beginEdit();
                dirty = true;
                ++editGeneration;
            }
        });
""" + text[end:]
    start = text.index("\t\tPattern validPattern = decimalSeparator")
    end = text.index("\n\n\t\tTextFormatter", start)
    text = text[:start] + r'        Pattern validPattern = Pattern.compile(String.format("^-?\\d{0,%d}(?:[.,]\\d{0,3})?$", wholeDigits));' + text[end:]
    text = replace_once(text, "textField.setTextFormatter(textFormatter);", """textField.setTextFormatter(textFormatter);
        // Labels start hidden; showing one never starts a second edit session.
        textField.setVisible(false);
        textField.setMinHeight(44);
        textField.setMinWidth(80);
        textField.setAccessibleText("Mål i millimeter. Brug komma eller punktum.");""")
    start = text.index("\t\t\t// Up arrow increases value by 1.0")
    end = text.index("\n\t\t\t// long timeMillis", start)
    text = text[:start] + """            if (event.getCode() == KeyCode.UP || event.getCode() == KeyCode.DOWN
                    || event.getCode() == KeyCode.PAGE_UP || event.getCode() == KeyCode.PAGE_DOWN) {
                event.consume();
                double step = event.getCode() == KeyCode.PAGE_UP || event.getCode() == KeyCode.PAGE_DOWN ? 10 : 1;
                if (event.getCode() == KeyCode.DOWN || event.getCode() == KeyCode.PAGE_DOWN) step = -step;
                nudge(step);
                return;
            }
""" + text[end:]
    start = text.index("\tprivate void runEnter() {")
    end = text.index("\n\tpublic ArrayList<Node> getTextField()", start)
    text = text[:start] + """    private void beginEdit() {
        if (!editing) {
            editing = true;
            dirty = false;
            initialValue = mostRecentValue;
            canceled = false;
            ++editGeneration;
        }
    }

    private void finishFocusEdit() {
        if (committing) return;
        if (editing && dirty && textField.isVisible()) {
            canceled = false;
            runEnter();
        } else {
            editing = false;
            ++editGeneration;
        }
    }

    private double readInput() {
        double value = Double.parseDouble(textField.getText().replace(',', '.')) + getMyOffset();
        if (!Double.isFinite(value)) throw new NumberFormatException("Non-finite measurement");
        return value;
    }

    private void nudge(double step) {
        beginEdit();
        double value;
        try { value = readInput(); }
        catch (NumberFormatException incomplete) { value = mostRecentValue; }
        double max = Math.pow(10, wholeDigits) - 0.001;
        value = Math.max(-max, Math.min(max, value + step));
        writeText(value);
        dirty = true;
        ++editGeneration;
    }

    private void runEnter() {
        if (committing) return;
        committing = true;
        try {
            if (!canceled) {
                try { mostRecentValue = readInput(); }
                catch (NumberFormatException incomplete) { canceled = true; }
            }
            if (Math.abs(mostRecentValue - initialValue) < 1e-6) canceled = true;
            if (canceled) mostRecentValue = initialValue;
            writeText(mostRecentValue);
            editing = false;
            dirty = false;
            ++editGeneration;
            textField.setVisible(false);
            mesh.setVisible(false);
            onChange.run();
        } finally { committing = false; }
    }

    private void writeText(double value) {
        writingText = true;
        try { textField.setText(String.format(Locale.getDefault(), "%.3f", value - getMyOffset())); }
        finally { writingText = false; }
    }

    public void setValue(double value) {
        if (!Platform.isFxApplicationThread()) {
            final long generation = editGeneration;
            Platform.runLater(() -> {
                if (generation == editGeneration) setValue(value);
            });
            return;
        }
        // Scene refreshes must not overwrite text, its baseline or caret.
        if (editing || textField.isFocused() || !Double.isFinite(value)) return;
        double max = Math.pow(10, wholeDigits) - 0.001;
        mostRecentValue = Math.max(-max, Math.min(max, value)) + 0.0;
        initialValue = mostRecentValue;
        writeText(mostRecentValue);
    }
""" + text[end:]
    start = text.index("\tpublic void hide() {")
    end = text.index("\n\tpublic void threeDTarget", start)
    text = text[:start] + """    public void hide() {
        if (editing && dirty && !committing) {
            canceled = false;
            runEnter();
            return;
        }
        editing = false;
        ++editGeneration;
        textField.setVisible(false);
        mesh.setVisible(false);
    }

    public void show() {
        if (textField.isVisible()) return;
        if (!editing) {
            canceled = false;
            initialValue = mostRecentValue;
            writeText(mostRecentValue);
        }
        textField.setVisible(true);
        mesh.setVisible(true);
    }
""" + text[end:]
    text = replace_once(text,
        "public double getMostRecentValue() {\n\t\tvalidate();\n\t\treturn mostRecentValue;\n\t}",
        "public double getMostRecentValue() {\n\t\treturn mostRecentValue;\n\t}")
    number.write_text(text, encoding="utf-8")
    return [resize, sprites, number]
