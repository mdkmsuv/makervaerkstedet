"""Capture mirror clicks and discard previews from cancelled/replaced sessions."""


def patch_mirror(fetch, replace_once, app_commit, generated):
    path, text = fetch("CaDoodle-Application", app_commit,
        "src/main/java/com/commonwealthrobotics/mirror/MirrorHandle.java", generated)
    text = replace_once(text, "private boolean active = false;", """private volatile boolean active = false;
    private volatile long sessionGeneration;
    private long previewGeneration;""")
    text = replace_once(text, "entered = event -> {", "entered = event -> {\n            if (!active) return;")
    text = replace_once(text, "onClickEvent = event -> {", """onClickEvent = event -> {
            if (!active || event.getButton() != javafx.scene.input.MouseButton.PRIMARY) return;""")
    start = text.index("\tprivate void setMyOperation() {")
    end = text.index("\n\tpublic void updateControls", start)
    text = text[:start] + '''    private void setMyOperation() {
        final long generation;
        final CaDoodleFile file;
        final Mirror operation;
        synchronized (this) {
            if (!active || selected == null || selected.isEmpty()) return;
            generation = sessionGeneration;
            file = ap.get();
            operation = new Mirror().setNames(List.copyOf(selected))
                    .setWorkplane(file.getWorkplane().copy()).setLocation(ax);
            operation.setCaDoodleFile(file);
        }
        // An accepted click remains an edit after leaving the tool. Its inputs
        // are captured now; only its old preview is invalidated by cancellation.
        com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
            if (ap.get() != file) return;
            try {
                Thread task = ap.addOp(operation);
                if (task != null) task.join();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
                return;
            }
            updateState(generation);
        });
    }
''' + text[end:]
    text = replace_once(text, "\t\tif (op != null)\n\t\t\top.setWorkplane(ap.get().getWorkplane());", "")
    start = text.index("\tpublic void initialize(Bounds b,")
    end = text.index("\n\tprivate void clearVisualizers()", start)
    text = text[:start] + '''    public void initialize(Bounds b, BowlerStudio3dEngine eng, List<CSG> t, List<String> sel,
            HashMap<CSG, MeshHolder> meshes) {
        final long generation;
        synchronized (this) {
            generation = ++sessionGeneration;
            ++previewGeneration;
            active = true;
            this.b = b;
            this.engine = eng;
            this.ta = List.copyOf(t);
            this.selected = List.copyOf(sel);
            this.meshes = meshes;
        }
        BowlerStudio.runLater(() -> {
            if (!active || generation != sessionGeneration) return;
            material.setDiffuseColor(myColor);
            mesh.setVisible(true);
            mesh.removeEventFilter(MouseEvent.MOUSE_EXITED, exited);
            mesh.removeEventFilter(MouseEvent.MOUSE_ENTERED, entered);
            mesh.removeEventFilter(MouseEvent.MOUSE_CLICKED, onClickEvent);
            mesh.addEventFilter(MouseEvent.MOUSE_EXITED, exited);
            mesh.addEventFilter(MouseEvent.MOUSE_ENTERED, entered);
            mesh.addEventFilter(MouseEvent.MOUSE_CLICKED, onClickEvent);
        });
        ap.removeListener(this);
        ap.addListener(this);
        updateState(generation);
    }

    public void updateState() {
        updateState(sessionGeneration);
    }

    private void updateState(long generation) {
        final long preview;
        final CaDoodleFile file;
        final Mirror operation;
        final List<CSG> shapes;
        synchronized (this) {
            if (!active || generation != sessionGeneration || selected == null) return;
            preview = ++previewGeneration;
            file = ap.get();
            operation = new Mirror().setNames(List.copyOf(selected))
                    .setWorkplane(file.getWorkplane().copy()).setLocation(ax);
            operation.setCaDoodleFile(file);
            shapes = List.copyOf(ta);
        }
        final List<CSG> computed = operation.process(shapes);
        BowlerStudio.runLater(() -> {
            synchronized (this) {
                if (!active || generation != sessionGeneration || preview != previewGeneration
                        || ap.get() != file) return;
                clearVisualizers();
                for (CSG indicator : computed) {
                    MeshView indicatorMesh = indicator.newMesh();
                    indicatorMesh.setMouseTransparent(true);
                    PhongMaterial material = new PhongMaterial();
                    if (indicator.isHole()) {
                        material.setDiffuseColor(new Color(0.25, 0.25, 0.25, 0.75));
                    } else {
                        Color c = indicator.getColor();
                        material.setDiffuseColor(new Color(c.getRed(), c.getGreen(), c.getBlue(), 0.75));
                    }
                    material.setSpecularColor(Color.WHITE);
                    indicatorMesh.setMaterial(material);
                    engine.addUserNode(indicatorMesh);
                    indicatorMesh.setVisible(false);
                    visualizers.put(indicator, indicatorMesh);
                }
            }
        });
    }
''' + text[end:]
    text = replace_once(text, "\tpublic void hide() {\n\t\tBowlerStudio.runLater(() -> {", """    public void hide() {
        final long generation;
        synchronized (this) {
            active = false;
            generation = ++sessionGeneration;
            ++previewGeneration;
        }
        BowlerStudio.runLater(() -> {
            if (active || generation != sessionGeneration) return;""")
    text = replace_once(text, "\tpublic void setActive(boolean active) {\n\t\tthis.active = active;\n\t}", """    public synchronized void setActive(boolean active) {
        if (this.active && !active) {
            ++sessionGeneration;
            ++previewGeneration;
        }
        this.active = active;
    }""")
    path.write_text(text, encoding="utf-8")
    return [path]
