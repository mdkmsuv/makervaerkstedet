"""Make native rotation cancellation discard previews and late input reliably."""


def patch_rotation(fetch, replace_once, app_commit, generated):
    handle, text = fetch("CaDoodle-Application", app_commit,
        "src/main/java/com/commonwealthrobotics/rotate/RotationHandle.java", generated)
    text = replace_once(text, "private boolean rotationStarted = false;",
        "private boolean rotationStarted = false;\n    private long previewGeneration;")
    text = replace_once(text, "EventHandler<? super MouseEvent> pressedEvent = ev -> {",
        "EventHandler<? super MouseEvent> pressedEvent = ev -> {\n"
        "            if (moveLock || ev.getButton() != javafx.scene.input.MouseButton.PRIMARY) return;\n"
        "            ++previewGeneration;")
    text = replace_once(text, "if (startAngleFound)\n\t\t\t\trunSaveAndReset();",
        "if (rotationStarted && startAngleFound && flagSaveChange && !moveLock) runSaveAndReset();")
    text = replace_once(text, "\t\t\tBowlerStudio.runLater(() -> {\n\t\t\t\tsession.setMode(SpriteDisplayMode.Rotating);",
        "\t\t\tfinal long generation = previewGeneration;\n"
        "            Runnable showControls = () -> {\n"
        "                if (generation != previewGeneration || !rotationStarted) return;\n"
        "\t\t\t\tsession.setMode(SpriteDisplayMode.Rotating);")
    text = replace_once(text, "\t\t\t\tTDnumber.show();\n\t\t\t});",
        "\t\t\t\tTDnumber.show();\n\t\t\t};\n"
        "            if (javafx.application.Platform.isFxApplicationThread()) showControls.run();\n"
        "            else BowlerStudio.runLater(showControls);")
    text = replace_once(text, "EventHandler<? super MouseEvent> dragged = event -> {",
        "EventHandler<? super MouseEvent> dragged = event -> {\n"
        "            if (!rotationStarted || moveLock) return;")
    # Keep the existing 22.5 degree snap semantics.  Once a drag has started,
    # accept subsequent samples even if the pick result moved from the image
    # ring to a child/overlay.  Drivers commonly report that transition while
    # crossing the ring edge; dropping those samples makes the preview hitch.
    text = replace_once(text,
        "if (event.getPickResult().getIntersectedNode() == controlCircle) {",
        "if (controlCircle.screenToLocal(event.getScreenX(), event.getScreenY()) != null || "
        "(event.getPickResult().getIntersectedNode() != null && "
        "(startAngleFound || event.getPickResult().getIntersectedNode() == controlCircle))) {")
    text = replace_once(text,
        "double x = result.getIntersectedPoint().getX();\n\t\tdouble y = result.getIntersectedPoint().getY();",
        "// Project onto the fixed ring plane: the model/overlay under the\n"
        "        // pointer can lie at another depth, or nothing may be picked.\n"
        "        javafx.geometry.Point2D projected = controlCircle.screenToLocal(event.getScreenX(), event.getScreenY());\n"
        "        double x, y;\n"
        "        if (projected != null) {\n"
        "            x = projected.getX(); y = projected.getY();\n"
        "        } else {\n"
        "            // Unattached scene graphs (including native input probes).\n"
        "            javafx.geometry.Point3D point = result.getIntersectedPoint();\n"
        "            if (result.getIntersectedNode() != controlCircle)\n"
        "                point = controlCircle.sceneToLocal(result.getIntersectedNode().localToScene(point));\n"
        "            x = point.getX(); y = point.getY();\n"
        "        }")
    start = text.index("\tprivate void runSaveAndReset() {")
    end = text.index("\n\tprivate void setSweepAngle", start)
    text = text[:start] + '''    public void abortInteraction() {
        ++previewGeneration;
        rotationStarted = false;
        flagSaveChange = false;
        startAngleFound = false;
        selected = false;
        rotAtCenter = null;
        // Escape discards the input before hide() can commit a dirty number.
        for (var node : TDnumber.getTextField()) {
            if (node instanceof javafx.scene.control.TextField field)
                field.fireEvent(new javafx.scene.input.KeyEvent(
                    javafx.scene.input.KeyEvent.KEY_PRESSED, "", "", javafx.scene.input.KeyCode.ESCAPE,
                    false, false, false, false));
        }
        TDnumber.hide();
        controlCircle.setVisible(false);
        arc.setVisible(false);
        TransformFactory.nrToAffine(new TransformNR(), viewRotation);
    }

    private void runSaveAndReset() {
        if (moveLock || rotAtCenter == null || !flagSaveChange) return;
        TransformNR toUpdate = rotAtCenter.copy();
        abortInteraction();
        session.setMode(SpriteDisplayMode.Default);
        done.toUpdate(toUpdate);
    }
''' + text[end:]
    text = replace_once(text,
        "BowlerStudio.runLater(() -> TransformFactory.nrToAffine(rotAtCenter, viewRotation));;",
        '''final long generation = previewGeneration;
        final TransformNR preview = rotAtCenter.copy();
        Runnable apply = () -> {
            if (generation == previewGeneration) TransformFactory.nrToAffine(preview, viewRotation);
        };
        if (javafx.application.Platform.isFxApplicationThread()) apply.run();
        else BowlerStudio.runLater(apply);''')
    text = replace_once(text, "\t\trotationStarted = false;\n\t\t// com.neuronrobotics.sdk.common.Log.error(\"Az camera",
        "\t\t// Keep the active gesture across camera/layout updates.\n\t\t// com.neuronrobotics.sdk.common.Log.error(\"Az camera")
    handle.write_text(text, encoding="utf-8")
    manager, text = fetch("CaDoodle-Application", app_commit,
        "src/main/java/com/commonwealthrobotics/rotate/RotationSessionManager.java", generated)
    text = replace_once(text, "public void resetSelected() {", '''public void abortInteraction() {
        for (RotationHandle handle : handles) handle.abortInteraction();
    }

    public void resetSelected() {''')
    manager.write_text(text, encoding="utf-8")
    sprites = generated / "src/main/java/com/commonwealthrobotics/controls/ControlSprites.java"
    text = sprites.read_text(encoding="utf-8")
    text = replace_once(text, "scaleSession.abortInteraction();",
        "scaleSession.abortInteraction();\n        rotationManager.abortInteraction();\n        mirror.cancel();")
    old = '''			try {
				ap.addOp(new MoveCenter().setLocation(tf).setNames(session.selectedSnapshot(), ap.get()));
			} catch (InvalidLocationMove e) {
				return;
			}'''
    new = '''            final var project = ap.get();
            final var names = List.copyOf(session.selectedSnapshot());
            final var rotation = tf.copy();
            com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
                if (ap.get() != project || names.isEmpty()) return;
                try {
                    Thread task = ap.addOp(new MoveCenter().setLocation(rotation).setNames(names, project));
                    if (task != null) task.join();
                } catch (InvalidLocationMove invalid) {
                    com.neuronrobotics.sdk.common.Log.error(invalid);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                }
            });'''
    text = replace_once(text, old, new)
    sprites.write_text(text, encoding="utf-8")
    return [handle, manager]
