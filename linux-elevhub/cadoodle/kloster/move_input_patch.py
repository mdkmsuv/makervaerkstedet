"""Ordered mouse gestures and immutable move commits for the pinned native engine."""

KERNEL_COMMIT = "457a64691504f1231ae5e6d5fd1d99577d96a5b4"


def patch_motion(fetch, replace_once, app_commit, generated):
    native, text = fetch("bowler-script-kernel", KERNEL_COMMIT,
        "src/main/java/com/neuronrobotics/bowlerkernel/Bezier3d/Manipulation.java", generated)
    text = replace_once(text, "private long pressedTime;", """private long pressedTime;
    private double pressSceneX, pressSceneY;
    private final ArrayList<Runnable> saveSnapshots = new ArrayList<>();
    private final java.util.concurrent.atomic.AtomicLong fxGestureGeneration =
            new java.util.concurrent.atomic.AtomicLong();

    public void addSaveSnapshotListener(Runnable capture) { saveSnapshots.add(capture); }

    /** Discard an unfinished application manipulation without firing save listeners. */
    public void abortDrag() {
        fxGestureGeneration.incrementAndGet();
        dragging = false;
        startCorrected = false;
        setState(DragState.IDLE);
        reset();
        for (Manipulation dependant : dependants) dependant.abortDrag();
    }

    private static void onFx(Runnable action) {
        if (javafx.application.Platform.isFxApplicationThread()) action.run();
        else BowlerKernel.runLater(action);
    }
""")
    text = replace_once(text, "public void fireSave() {", """public void fireSave() {
        // Freeze application pose/name/workplane snapshots before another gesture.
        // Existing legacy listeners still retain their asynchronous contract.
        if (!saveSnapshots.isEmpty()) onFx(() -> {
            for (Runnable capture : saveSnapshots) capture.run();
        });""")
    start = text.index("\tprivate void pressed(MouseEvent event) {")
    end = text.index("\n\tprivate void dragged(", start)
    text = text[:start] + '''\tprivate void pressed(MouseEvent event) {
        if (!resizeAllowed) return;
        fxGestureGeneration.incrementAndGet();
        pressSceneX = event.getSceneX();
        pressSceneY = event.getSceneY();
        startXpix = event.getScreenX();
        startYpix = event.getScreenY();
        setState(DragState.Dragging);
        dragging = false;
        startCorrected = false;
        for (Manipulation dependant : dependants) dependant.dragging = false;
        // The handle's bubbling MOUSE_PRESSED handler still needs to capture
        // originalBounds/beingUpdated. Consume the actual drag, not this press.
    }
''' + text[end:]
    start = text.index("\t\tlong l = System.currentTimeMillis() - pressedTime;")
    end = text.index("\n\t\tif (resizeAllowed", start)
    text = text[:start] + '''        // Ignore click jitter, not deliberate fast drags.
        if (!dragging && Math.hypot(event.getSceneX() - pressSceneX,
                event.getSceneY() - pressSceneY) < 3) return;
''' + text[end:]
    text = replace_once(text,
        "\t\t\tstartXpix = event.getScreenX();\n\t\t\tstartYpix = event.getScreenY();",
        "\t\t\t// Pixel origin was captured at press; keep it for every drag sample.")
    text = text.replace("Double.isFinite(y) && Double.isFinite(x)",
                        "Double.isFinite(y) && Double.isFinite(x) && Double.isFinite(z)")
    # Native mouse events already run on FX. Delayed previews can otherwise land
    # after release/cancel and leave an unsaved transform in the scene graph.
    text = text.replace("BowlerKernel.runLater(() -> {\n\t\t\t\t\t\tsetDragging(event);",
                         "final long gesture = fxGestureGeneration.get();\n\t\t\t\t\tonFx(() -> {\n\t\t\t\t\t\tif (gesture != fxGestureGeneration.get() || getState() != DragState.Dragging) return;\n\t\t\t\t\t\tsetDragging(event);")
    text = replace_once(text,
        "BowlerKernel.runLater(() -> {\n\t\t\tTransformFactory.nrToAffine(global, manipulationMatrix);",
        "final long gesture = fxGestureGeneration.get();\n\t\t\tonFx(() -> {\n\t\t\tif (gesture != fxGestureGeneration.get()) return;\n\t\t\tTransformFactory.nrToAffine(global, manipulationMatrix);")
    native.write_text(text, encoding="utf-8")

    session, text = fetch("CaDoodle-Application", app_commit,
        "src/main/java/com/commonwealthrobotics/controls/SelectionSession.java", generated)
    # Grouping must refresh the selected controls promptly (the pinned source
    # accidentally waits 100 minutes).
    text = replace_once(text, "java.time.Duration.ofMinutes(100)", "java.time.Duration.ofMillis(100)")
    # One persistent mesh filter already handles both selection and movement.
    # Reattaching a second native handler on selection delivers every drag twice.
    text = text.replace("meshView.removeEventFilter(MouseEvent.ANY, mouseMover);", "// Persistent move filter retained.")
    text = replace_once(text,
        "if (!c.isLock() && !c.isMotionLock() && !c.isInGroup())\n\t\t\t\t\t\tmeshView.addEventFilter(MouseEvent.ANY, mouseMover);",
        "// The persistent lockable move filter is already attached to this mesh.")
    start = text.index("\t\tmanipulation.addSaveListener(() -> {")
    end = text.index("\n\t\tmanipulation.addEventListener", start)
    text = text[:start] + '''        manipulation.addSaveSnapshotListener(() -> {
            if (intitialization) return;
            final var project = ap.get();
            final List<String> names = List.copyOf(selectedSnapshot());
            final TransformNR pose = manipulation.getGlobalPoseInReferenceFrame().copy();
            manipulation.abortDrag();
            if (names.isEmpty()) return;
            com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
                if (ap.get() != project) return;
                try {
                    Thread task = ap.addOp(new MoveCenter().setLocation(pose).setNames(names, project));
                    if (task != null) task.join();
                } catch (InvalidLocationMove invalid) {
                    Log.error(invalid);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                } finally {
                    BowlerStudio.runLater(() -> getControls().setMode(SpriteDisplayMode.Default));
                }
            });
        });
''' + text[end:]
    text = replace_once(text, "public void updateControls() {", '''public void abortInteraction() {
        manipulation.abortDrag();
        getControls().abortInteraction();
        updateControls();
    }

    public void updateControls() {''')
    session.write_text(text, encoding="utf-8")

    sprites = generated / "src/main/java/com/commonwealthrobotics/controls/ControlSprites.java"
    text = sprites.read_text(encoding="utf-8")
    start = text.index("\t\tzMoveManipulator.addSaveListener(() -> {")
    end = text.index("\n\t\tzMoveManipulator.addEventListener", start)
    text = text[:start] + '''        zMoveManipulator.addSaveSnapshotListener(() -> {
            final var project = ap.get();
            final List<String> names = List.copyOf(session.selectedSnapshot());
            final TransformNR pose = zMoveManipulator.getGlobalPoseInReferenceFrame().copy();
            zMoveManipulator.abortDrag();
            com.commonwealthrobotics.kloster.ModelEditQueue.submit(ap, () -> {
                if (ap.get() != project) return;
                try {
                    Thread task = ap.addOp(new MoveCenter().setLocation(pose).setNames(names, project));
                    if (task != null) task.join();
                } catch (InvalidLocationMove invalid) {
                    Log.error(invalid);
                } catch (InterruptedException interrupted) {
                    Thread.currentThread().interrupt();
                } finally {
                    BowlerKernel.runLater(() -> {
                        TransformFactory.nrToAffine(new TransformNR(), zMoveOffsetFootprint);
                        zmoving = false;
                        setMode(SpriteDisplayMode.Default);
                        updateLines();
                    });
                }
            });
        });
''' + text[end:]
    text = replace_once(text, "void resetManipulator(Bounds b2) {", '''public void abortInteraction() {
        zMoveManipulator.abortDrag();
        scaleSession.abortInteraction();
        TransformFactory.nrToAffine(new TransformNR(), zMoveOffsetFootprint);
        xymoving = false;
        zmoving = false;
        setMode(SpriteDisplayMode.Default);
        updateLinesAndCubes();
    }

    void resetManipulator(Bounds b2) {''')
    sprites.write_text(text, encoding="utf-8")
    return [native, session]
