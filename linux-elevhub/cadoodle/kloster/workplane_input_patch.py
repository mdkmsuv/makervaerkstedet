"""Resolve a touch press from its own pick and invalidate stale placement work."""


def patch_workplane_input(path, replace_once):
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "private boolean active;", """private volatile boolean active;
    private volatile boolean clickPending;
    private final java.util.concurrent.atomic.AtomicLong placementGeneration = new java.util.concurrent.atomic.AtomicLong();
    private final java.util.concurrent.atomic.AtomicLong pickSequence = new java.util.concurrent.atomic.AtomicLong();""")
    text = replace_once(text, "\t\tBowlerKernel.runLater(() -> {\n\t\t\tactive = false;", """        active = false;
        clickPending = false;
        final long generation = placementGeneration.incrementAndGet();
        BowlerKernel.runLater(() -> {
            if (generation != placementGeneration.get()) return;""")
    text = replace_once(text, "\tpublic void activate(boolean enableGroundPick) {\n\t\tactive = true;", """    public void activate(boolean enableGroundPick) {
        final long generation = placementGeneration.incrementAndGet();
        clickPending = false;
        active = true;""")
    text = replace_once(text, "\t\t\t// com.neuronrobotics.sdk.common.Log.debug(\"Starting workplane listeners\");", """            if (!active || generation != placementGeneration.get()) return;
            // Start only the current placement session's listeners.""")
    old = '''			PickResult pickResult = ev.getPickResult();
			Node intersectedNode = pickResult.getIntersectedNode();

			if (ev.getEventType() == MouseEvent.MOUSE_PRESSED) {
				doClickEvent(ev);

			} else if ((ev.getEventType() == MouseEvent.MOUSE_MOVED)
					|| (ev.getEventType() == MouseEvent.MOUSE_DRAGGED)) {
				session.submit(() -> {'''
    new = '''            if (!active || clickPending) return;
            final boolean confirm = ev.getEventType() == MouseEvent.MOUSE_PRESSED;
            if (confirm && ev.getButton() != javafx.scene.input.MouseButton.PRIMARY) return;
            PickResult pickResult = ev.getPickResult();
            if (pickResult == null || pickResult.getIntersectedNode() == null
                    || pickResult.getIntersectedPoint() == null) return;
            Node intersectedNode = pickResult.getIntersectedNode();

            if (confirm || ev.getEventType() == MouseEvent.MOUSE_MOVED
                    || ev.getEventType() == MouseEvent.MOUSE_DRAGGED) {
                final long generation = placementGeneration.get();
                final long request = pickSequence.incrementAndGet();
                final var file = ap.get();
                final boolean groundPick = intersectedNode == wpPick.intersectionNode
                        || intersectedNode == engine.getWorkplaneGroup().intersectionNode;
                if (confirm) {
                    clickPending = true;
                    ev.consume();
                }
                session.submit(() -> {
                    if (!active || generation != placementGeneration.get()
                            || request != pickSequence.get() || ap.get() != file) return;'''
    text = replace_once(text, old, new)
    old = '''					if ((intersectedNode == wpPick.intersectionNode)) {
						if (updater != null)
							updater.setWorkplaneLocation(screenLocation);

						screenLocation = ap.get().getWorkplane().times(screenLocation);
					} else if (updater != null)
						updater.setWorkplaneLocation(ap.get().getWorkplane().inverse().times(screenLocation));
					TransformNR toSet = screenLocation;
					BowlerKernel.runLater(() -> setCurrentAbsolutePose(toSet));'''
    new = '''                    final TransformNR updaterPose;
                    if (intersectedNode == wpPick.intersectionNode) {
                        updaterPose = screenLocation.copy();
                        screenLocation = file.getWorkplane().times(screenLocation);
                    } else {
                        updaterPose = file.getWorkplane().inverse().times(screenLocation);
                    }
                    final TransformNR toSet = screenLocation;
                    BowlerKernel.runLater(() -> {
                        if (!active || generation != placementGeneration.get()
                                || request != pickSequence.get() || ap.get() != file) return;
                        if (updater != null) updater.setWorkplaneLocation(updaterPose);
                        setCurrentAbsolutePose(toSet);
                        if (confirm) {
                            setClickOnGround(groundPick);
                            doClickEvent(ev);
                        }
                    });'''
    text = replace_once(text, old, new)
    start = text.index("\tpublic void doClickEvent(MouseEvent ev) {")
    end = text.index("\n\tprivate Polygon getPolygonFromFaceIndex", start)
    text = text[:start] + '''    public void doClickEvent(MouseEvent ev) {
        // ResizingHandle also calls this after explicitly pinning a pose.
        // Keep that entry point, while handle() resolves ordinary presses first.
        if (!active || ev.getButton() != javafx.scene.input.MouseButton.PRIMARY) return;
        clicked = true;
        ev.consume();
        cancel();
        engine.getWorkplaneGroup().setMouseTransparent(true);
        engine.groundToNormal();
        session.getControls().hideRotationHandles();
        wpPick.setMouseTransparent(true);
        session.getControls().setMode(SpriteDisplayMode.Default);
    }
''' + text[end:]
    path.write_text(text, encoding="utf-8")
