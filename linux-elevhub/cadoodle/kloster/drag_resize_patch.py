#!/usr/bin/env python3
"""Patch the generated ResizeSessionManager save path after numeric patches.

Usage: python drag_resize_patch.py path/to/generated/ResizeSessionManager.java
The operation is intentionally source-local and idempotent.
"""
from pathlib import Path
import re
import sys


def patch_drag_resize(generated) -> Path:
    path = Path(generated)
    text = path.read_text(encoding="utf-8")
    if "addSaveSnapshotListener" in text:
        print("already patched")
        return path
    if "c.manipulator.addSaveListener(() -> {" not in text:
        raise SystemExit("expected pinned save-listener block was not found")
    text = text.replace("import java.util.Map;", "import java.util.Map;\nimport java.util.WeakHashMap;\nimport java.util.concurrent.atomic.AtomicLong;", 1)
    if "import com.commonwealthrobotics.kloster.ModelEditQueue;" not in text:
        text = text.replace("import com.commonwealthrobotics.ActiveProject;", "import com.commonwealthrobotics.ActiveProject;\nimport com.commonwealthrobotics.kloster.ModelEditQueue;", 1)
    text = text.replace("\tprivate ControlSprites controlSprites;", "\tprivate ControlSprites controlSprites;\n\tprivate final AtomicLong gestureGeneration = new AtomicLong();\n\tprivate final Map<MeshView, List<javafx.scene.transform.Transform>> previewBaseTransforms = new WeakHashMap<>();", 1)

    start = text.index("\t\tfor (ResizingHandle c : controls) {\n\t\t\tmeshControls.add(c.getMesh());")
    end = text.index("\n\t}\n\n\tprivate void updateHandleCenters", start)
    replacement = r'''		for (ResizingHandle c : controls) {
			meshControls.add(c.getMesh());
			c.manipulator.setFrameOfReference(() -> ap.get().getWorkplane());
			c.manipulator.addSaveSnapshotListener(() -> {
				if (beingUpdated != c)
					return;
				final long generation = gestureGeneration.incrementAndGet();
				final var project = ap.get();
				final List<String> names = List.copyOf(session.selectedSnapshot());
				final TransformNR wp = project.getWorkplane().copy();
				final boolean allowResize = resizeAllowed;
				final TransformNR rrSnapshot = rightRear.getCurrentInReferenceFrame().copy();
				final TransformNR lfSnapshot = leftFront.getCurrentInReferenceFrame().copy();
				final TransformNR tcSnapshot = topCenter.getCurrentInReferenceFrame().copy();

				// End the interactive state before leaving the FX callback. These
				// calls do not enqueue a Resize and cannot save a second gesture.
				beingUpdated = null;
				originalBounds = null;
				for (ResizingHandle ctrl : controls) {
					ctrl.manipulator.abortDrag();
				}
				controlSprites.setMode(SpriteDisplayMode.Default);

				ModelEditQueue.submit(ap, () -> {
				if (project != ap.get())
						return;
					try {
						Resize nativeResize = new Resize().setNames(names).setWorkplane(wp)
								.setResize(tcSnapshot, lfSnapshot, rrSnapshot);
						if (allowResize)
							ap.addOp(nativeResize).join();
					} catch (Exception ex) {
						Log.error(ex);
					}
					BowlerStudio.runLater(() -> {
						if (generation == gestureGeneration.get() && project == ap.get())
							threeDTarget();
					});
				});
			});
		}'''
    text = text[:start] + replacement + text[end:]

    # A selection rebuild can replace CSG identity while a drag is finishing.
    text = text.replace("\t\tMeshHolder meshHolder = meshes.get(key);\n\t\tMeshView old = meshHolder.display; // lookup with original key", "\t\tMeshHolder meshHolder = meshes.get(key);\n\t\tif (meshHolder == null || meshHolder.display == null)\n\t\t\treturn;\n\t\tMeshView old = meshHolder.display; // lookup with original key", 1)
    # Keep resize previews in the retained MeshView. The old path rebuilt a
    # complete CSG mesh for every mouse sample, which made touch drags stutter.
    # Native Resize remains the authoritative operation on save.
    preview_start = text.index("\tpublic void rescaleMeshes(Affine workplaneOffset, Transform xyzScale) {")
    preview_end = text.index("\n\tprivate void regenerateMesh", preview_start)
    preview_method = '''\tpublic void rescaleMeshes(Affine workplaneOffset, Transform xyzScale) {
\t\tTransformNR workplaneNR = TransformFactory.affineToNr(workplaneOffset);
\t\tTransform workplaneInverse = TransformFactory.nrToCSG(workplaneNR.inverse());
\t\tTransform workplaneTransform = TransformFactory.nrToCSG(workplaneNR);
\t\tjavax.vecmath.Matrix4d matrix = new javax.vecmath.Matrix4d(workplaneTransform.getInternalMatrix());
\t\tmatrix.mul(xyzScale.getInternalMatrix());
\t\tmatrix.mul(workplaneInverse.getInternalMatrix());
\t\tAffine preview = new Affine(matrix.m00, matrix.m01, matrix.m02, matrix.m03,
\t\t\t\tmatrix.m10, matrix.m11, matrix.m12, matrix.m13,
\t\t\t\tmatrix.m20, matrix.m21, matrix.m22, matrix.m23);
\t\tfor (CSG c : session.getCurrentStateSelected())
\t\t\tapplyPreviewTransform(c, preview);
\t\tcontrolSprites.setMode(SpriteDisplayMode.Resize);
\t}

\tprivate void applyPreviewTransform(CSG key, Affine preview) {
\t\tMeshHolder holder = meshes.get(key);
\t\tif (holder == null || holder.display == null)
\t\t\treturn;
\t\tMeshView view = holder.display;
\t\tpreviewBaseTransforms.computeIfAbsent(view, ignored -> new ArrayList<>(view.getTransforms()));
\t\tview.getTransforms().setAll(previewBaseTransforms.get(view));
\t\tview.getTransforms().add(new Affine(preview));
\t}
'''
    text = text[:preview_start] + preview_method + text[preview_end:]

    old_mesh = """\t\tMeshView fresh = scaledCSG.newMesh();
\t\tfresh.setMaterial(old.getMaterial());
\t\tfresh.getTransforms().setAll(old.getTransforms());
\t\tfresh.setMouseTransparent(old.isMouseTransparent());

\t\tengine.removeUserNode(old);
\t\tengine.addUserNode(fresh);
\t\tmeshHolder.display = fresh;"""
    new_mesh = """\t\tMeshView fresh = scaledCSG.newMesh();
\t\t// Retain the registered MeshView identity so handlers, filters and move bindings survive.
\t\told.setMesh(fresh.getMesh());
\t\tList<javafx.scene.transform.Transform> base = previewBaseTransforms.remove(old);
\t\tif (base != null)
\t\t\told.getTransforms().setAll(base);"""
    if old_mesh not in text:
        raise SystemExit("expected mesh replacement block was not found")
    text = text.replace(old_mesh, new_mesh, 1)

    marker = "\n\tpublic double getViewScale()"
    method = r'''
	/** Abort the active resize without creating a native Resize operation. */
	public void abortInteraction() {
		gestureGeneration.incrementAndGet();
		beingUpdated = null;
		originalBounds = null;
		for (ResizingHandle ctrl : controls) {
			ctrl.manipulator.abortDrag();
			ctrl.resetSelected();
		}
		for (CSG c : session.getCurrentStateSelected())
			regenerateMesh(c, c);
		session.updateControls();
		controlSprites.setMode(SpriteDisplayMode.Default);
	}
'''
    if "public void abortInteraction()" not in text:
        text = text.replace(marker, method + marker, 1)
    path.write_text(text, encoding="utf-8")
    print(f"patched {path}")
    return path


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: drag_resize_patch.py GENERATED_ResizeSessionManager.java")
    patch_drag_resize(sys.argv[1])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

