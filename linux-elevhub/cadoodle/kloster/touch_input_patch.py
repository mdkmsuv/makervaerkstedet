"""Wire touch arbitration into the viewport, before child mouse handlers."""

def patch_touch(fetch, replace_once, app_commit, generated):
    selection, text = fetch("CaDoodle-Application", app_commit,
        "src/main/java/com/commonwealthrobotics/SelectionBox.java", generated)
    text = replace_once(text, "\tpublic void cancle() {\n\t\t// engine.removeUserNode(selectionPlane);\n\t}", '''
    public void cancle() {
        active = false;
        start = false;
        rect1.setVisible(false);
        rect2.setVisible(false);
        overlayPane2D.getChildren().removeAll(rect1, rect2);
        for (MeshHolder holder : session.getMeshes().values())
            if (holder != null && holder.display != null) holder.display.setMouseTransparent(false);
    }''')
    text = replace_once(text,
        "setSelectionPlane(new Cube(20000, 20000, 0.001).toCSG().newMesh());",
        "setSelectionPlane(new Cube(20000, 20000, 0.001).toCSG().newMesh());\n"
        "        getSelectionPlane().setId(\"kloster-selection-plane\");")
    selection.write_text(text, encoding="utf-8")
    session = generated / "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java"
    text = session.read_text(encoding="utf-8")
    anchor = "\tprivate void addToSelected(CSG name) {"
    text = replace_once(text, anchor, '''
    /** Toggle one actual model after a completed touch tap. No mouse event or move is synthesized. */
    public void toggleTouchSelection(CSG name) {
        if (name == null || name.isHide() || name.isInGroup()
                || ap.get().isOperationRunning()) return;
        abortInteraction();
        cancelOperationModes();
        if (isSelected(name)) removeSelected(name);
        else addToSelected(name);
        fireSelectionChanged();
        if (getMode() != SpriteDisplayMode.Align)
            setMode(SpriteDisplayMode.Default);
        updateRobotLab.run();
        updateControlsDisplayOfSelected();
    }

    /** Apply a default-mode tap even if a driver did not emit a mouse press. */
    public void selectOnlyTouchModel(CSG name) {
        if (name == null || name.isHide() || name.isInGroup()
                || ap.get().isOperationRunning()) return;
        abortInteraction();
        cancelOperationModes();
        if (!isSelected(name) || getSelected().size() != 1) {
            clearInternalSelection();
            addToSelected(name);
        }
        fireSelectionChanged();
        if (getMode() != SpriteDisplayMode.Align)
            setMode(SpriteDisplayMode.Default);
        updateRobotLab.run();
        updateControlsDisplayOfSelected();
    }

''' + anchor)
    session.write_text(text, encoding="utf-8")
    controller = generated / "src/main/java/com/commonwealthrobotics/MainController.java"
    text = controller.read_text(encoding="utf-8")
    anchor = "selectionBox = new SelectionBox(session, view3d, engine, ap, paneOverlay2D);"
    text = replace_once(text, anchor, anchor + '''
            BowlerStudio.runLater(() -> {
                var touchGate = new com.commonwealthrobotics.input.TouchGestureGate(
                    () -> { selectionBox.cancle(); session.abortInteraction(); },
                    new com.commonwealthrobotics.input.TouchGestureGate.ZoomSink() {
                        private void zoom(double factor) {
                            if (Double.isFinite(factor) && factor > 0) {
                                var camera = engine.getFlyingCamera();
                                camera.setZoomDepth(camera.getZoomDepth() / factor);
                            }
                        }
                        public void onTouchZoomFactor(double factor) { zoom(factor); }
                        public void onNativeZoomFactor(double factor) { zoom(factor); }
                    });
                touchGate.install(view3d);
                view3d.addEventFilter(javafx.scene.input.MouseEvent.MOUSE_PRESSED, event -> {
                    if (event.isPrimaryButtonDown()
                            && (com.commonwealthrobotics.kloster.ModelEditQueue.isBusy(ap)
                                || ap.get().isOperationRunning())) event.consume();
                });
            });''')
    controller.write_text(text, encoding="utf-8")
    # move_input_patch already includes SelectionSession in the javac source set.
    return [selection]
